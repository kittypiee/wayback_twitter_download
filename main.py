import os
import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime
import waybackpy # 添加对 waybackpy 的导入

# --- 配置区 ---
# 图片保存的根文件夹
IMAGE_DOWNLOAD_FOLDER = "twitter_images"
# 用来记录所有已下载图片URL的日志文件，用于去重
DOWNLOADED_URLS_LOG = "downloaded_urls.log"
# 记录图片下载/保存失败的日志文件
IMAGE_FAILED_LOG = "image_failures.txt"
# 记录快照页面加载/解析失败的日志文件
SNAPSHOT_FAILED_LOG = "snapshot_failures.txt"
# 网络请求重试次数
MAX_RETRIES = 1
# 每次重试的等待时间（秒）
RETRY_DELAY = 1.5

# --- 全局会话和请求头 ---
# 使用Session可以保持连接，并统一设置请求头
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
})

def setup_environment(twitter_id):
    """
    根据推特ID创建特定的下载文件夹和日志文件路径。
    """
    user_specific_folder = os.path.join(IMAGE_DOWNLOAD_FOLDER, twitter_id)
    if not os.path.exists(user_specific_folder):
        print(f"创建文件夹: {user_specific_folder}")
        os.makedirs(user_specific_folder)

    user_specific_log = os.path.join(user_specific_folder, DOWNLOADED_URLS_LOG)
    snapshot_failed_log = os.path.join(user_specific_folder, SNAPSHOT_FAILED_LOG)
    image_failed_log = os.path.join(user_specific_folder, IMAGE_FAILED_LOG)

    return user_specific_folder, user_specific_log, snapshot_failed_log, image_failed_log

def log_failure(message, log_path):
    """
    将失败信息记录到指定的日志文件中。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"!!! 无法写入失败日志: {e}")

def load_downloaded_urls(log_path):
    """
    从日志文件中加载已经下载过的图片URL，用于去重。
    """
    if not os.path.exists(log_path):
        return set()
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f)
    except Exception as e:
        print(f"读取日志文件 {log_path} 时出错: {e}")
        return set()

def get_with_retries(url, timeout=30):
    """
    带有重试机制的GET请求函数。
    """
    for attempt in range(MAX_RETRIES):
        try:
            response = SESSION.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            print(f"  -> 请求失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  -> {RETRY_DELAY}秒后重试...")
                time.sleep(RETRY_DELAY)
            else:
                return None

def get_wayback_snapshots(twitter_id, failed_log_path, start_timestamp=None, end_timestamp=None):
    """
    修改过的函数：使用 waybackpy 获取用户的所有历史页面快照。
    获取所有以 'https://twitter.com/{twitter_id}' 开头的URL。
    """
    print(f"\n[步骤 1/3] 正在使用 waybackpy 查询ID: {twitter_id} 的历史快照...")
    target_url_base = f"https://twitter.com/{twitter_id}"
    user_agent = SESSION.headers.get("User-Agent")
    all_snapshots = []

    try:
        # 初始化 Wayback Machine CDX Server API 客户端
        wayback = waybackpy.WaybackMachineCDXServerAPI(
            target_url_base,
            user_agent,
            match_type='prefix', # 获取此路径下的所有URL
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp
        )
        print("正在获取所有匹配的快照记录，这可能需要一些时间...")

        # 该库返回一个生成器，将其转换为元组列表
        snapshots_generator = wayback.snapshots()
        for snapshot in snapshots_generator:
             # 我们只关心单个推文状态的快照
            if f"/{twitter_id}" in snapshot.original:
              all_snapshots.append((snapshot.timestamp, snapshot.original))

    except Exception as e:
        error_msg = f"WAYBACKPY_失败: 获取 {target_url_base} 的快照失败。错误: {e}"
        print(error_msg)
        log_failure(error_msg, failed_log_path)
        return []

    # 对结果进行去重和排序，因为多个记录可能指向相同的内容
    unique_snapshots = sorted(list(set(all_snapshots)))
    print(f"查询完成。找到 {len(unique_snapshots)} 个不重复的历史推文快照。")
    return unique_snapshots


def get_image_urls_from_page(page_url, twitter_id, snapshot_failed_log_path):
    """
    从单个Wayback Machine页面中解析出指定ID发布的图片URL。
    优先尝试解析HTML页面，如果未找到推文，则回退到解析JSON API响应。
    """
    print(f"正在解析页面: {page_url}")
    response = get_with_retries(page_url)
    if not response:
        log_failure(f"页面抓取失败: 无法获取页面内容 {page_url}", snapshot_failed_log_path)
        return []

    # --- 策略1: 尝试解析HTML页面 ---
    try:
        soup = BeautifulSoup(response.text, 'html.parser')
        html_image_urls = []
        html_found_tweets = False

        # --- 子策略1.1: 尝试解析新版 (2022+) Twitter 页面结构 (React App) ---
        articles = soup.find_all('article', attrs={'data-testid': 'tweet'})
        if articles:
            print(f"检测到新版HTML布局，找到 {len(articles)} 个 <article> 标签。正在筛选...")
            for article in articles:
                status_link = article.find('a', href=re.compile(f'/{twitter_id}/status/', re.IGNORECASE))
                if status_link:
                    html_found_tweets = True # 只要找到属于用户的推文容器，就标记为成功
                    meta_thumbnails = article.find_all('meta', itemprop='thumbnailUrl', content=True)
                    if meta_thumbnails:
                        for tag in meta_thumbnails:
                            url = tag['content']
                            if 'pbs.twimg.com/media/' in url:
                                base_url = re.sub(r':(large|medium|small|thumb)$', '', url)
                                high_res_url = base_url.split('?')[0] + ".jpg"
                                html_image_urls.append(high_res_url)
                    else:
                        photo_divs = article.find_all('div', attrs={'data-testid': 'tweetPhoto'})
                        for div in photo_divs:
                            img_tag = div.find('img', src=True)
                            if img_tag and 'pbs.twimg.com/media/' in img_tag['src']:
                                img_src = img_tag['src']
                                original_url = img_src.split('im_/')[-1] if 'im_/' in img_src else img_src
                                base_url = original_url.split('?')[0]
                                high_res_url = base_url + ".jpg"
                                html_image_urls.append(high_res_url)

        # --- 子策略1.2: 回退到旧版 (pre-2022) 页面结构 ---
        if not html_found_tweets:
            tweets = soup.find_all('div', class_=re.compile(r'\btweet\b'), attrs={'data-screen-name': twitter_id})
            if tweets:
                html_found_tweets = True # 标记为成功
                print(f"检测到旧版HTML布局，找到 {len(tweets)} 条推文。")
                for tweet in tweets:
                    media_containers = tweet.find_all('div', attrs={'data-image-url': True})
                    for container in media_containers:
                        base_url = re.sub(r':(large|medium|small|thumb)$', '', container['data-image-url'])
                        html_image_urls.append(base_url)
                    meta_tags = tweet.find_all('meta', property='og:image', content=True)
                    for tag in meta_tags:
                        url = tag['content']
                        if 'pbs.twimg.com/media/' in url:
                            base_url = re.sub(r':(large|medium|small|thumb)$', '', url)
                            html_image_urls.append(base_url)

        # 如果通过HTML解析找到了属于该用户的推文，则直接返回结果（即使没有图片）
        if html_found_tweets:
            unique_urls = sorted(list(set(html_image_urls)))
            if unique_urls:
                print(f"从HTML页面解析出 {len(unique_urls)} 个不重复的图片链接。")
            else:
                print("在HTML中找到了属于该用户的推文，但推文中不包含图片。")
            return unique_urls

        # 如果HTML解析未找到任何相关推文，则继续尝试JSON解析
        print("在HTML页面中未找到属于该用户的推文，将尝试作为JSON进行解析...")

    except Exception as e:
        # 这个异常捕获的是BeautifulSoup的解析错误，不是请求错误
        error_msg = f"HTML页面解析时发生意外错误: {page_url} | 错误: {e}"
        print(f"  [失败] {error_msg}")
        log_failure(error_msg, snapshot_failed_log_path)
        print("将继续尝试JSON解析...")

    # --- 策略2: 如果HTML解析未找到推文，则回退到JSON API响应 ---
    try:
        data = response.json()
        #print(data)
        print("尝试作为API JSON数据解析...")

        json_image_urls = []

        media_map = {}
        if 'includes' in data and 'media' in data['includes']:
            for media_item in data['includes']['media']:
                if media_item.get('type') == 'photo' and 'url' in media_item:
                    media_map[media_item['media_key']] = media_item['url']

        target_user_id = None
        if 'includes' in data and 'users' in data['includes']:
            for user in data['includes']['users']:
                if user.get('username', '').lower() == twitter_id.lower():
                    target_user_id = user.get('id')
                    break

        if target_user_id:
            all_tweets = []
            if 'data' in data:
                if isinstance(data['data'], list):
                    all_tweets.extend(data['data'])
                elif isinstance(data['data'], dict):
                    all_tweets.append(data['data'])
            if 'includes' in data and 'tweets' in data['includes']:
                all_tweets.extend(data['includes']['tweets'])

            for tweet in all_tweets:
                if tweet.get('author_id') == target_user_id:
                    if 'attachments' in tweet and 'media_keys' in tweet['attachments']:
                        for media_key in tweet['attachments']['media_keys']:
                            if media_key in media_map:
                                json_image_urls.append(media_map[media_key])
        else:
            print(f"警告: 在JSON数据中未找到用户 @{twitter_id}。")

        if json_image_urls:
            unique_urls = sorted(list(set(json_image_urls)))
            print(f"从JSON数据中解析到 {len(unique_urls)} 个不重复的图片链接。")
            print(unique_urls)
            return unique_urls
        else:
             print("JSON中也未找到属于该用户的图片。")

    except json.JSONDecodeError:
        print("响应不是有效的JSON。HTML和JSON解析均告失败。")
        log_failure(f"解析失败: 响应既不是有效HTML也不是JSON {page_url}", snapshot_failed_log_path)
    except Exception as e:
        error_msg = f"JSON解析时发生意外错误: {page_url} | 错误: {e}"
        print(f"  [失败] {error_msg}")
        log_failure(error_msg, snapshot_failed_log_path)

    return [] # 如果所有方法都失败，则返回空列表

def _transform_to_raw_url(url: str) -> str | None:
    """
    一个内部辅助函数，用于将标准的Wayback图片链接转换为指向原始文件内容的链接。
    如果链接格式不正确，则返回 None。
    """
    try:
        if '/web/' not in url:
            return None # 不是一个有效的Wayback Machine链接

        parts = url.split('/web/')[1].split('/', 1)
        timestamp = parts[0]
        original_image_url = parts[1]

        # 移除扩展名并添加请求原始图片的参数
        base_image_url = original_image_url.rsplit('.', 1)[0]
        modified_image_url = f"{base_image_url}?format=jpg&name=orig"

        wayback_base_prefix = "https://web.archive.org/web/"
        # 重新组装，在时间戳后加入 'if_'
        return f"{wayback_base_prefix}{timestamp}if_/{modified_image_url}"
    except (IndexError, ValueError):
        # 如果URL分割失败，说明格式有问题
        return None

def download_image(twitter_id,image_url, download_folder, downloaded_urls_set, log_path, image_failed_log_path):
    """
    下载单张图片，具有更清晰的逻辑和输出，并带有失败重试机制。

    :param image_url: 从页面解析出的原始图片链接。
    :param download_folder: 图片保存的目标文件夹。
    :param downloaded_urls_set: 内存中已下载URL的集合，用于去重。
    :param log_path: 成功下载日志文件的路径。
    :param image_failed_log_path: 失败下载日志文件的路径。
    """
    # 步骤 1: 规范化URL并检查是否已下载或文件已存在
    try:
        # 移除参数和后缀，用于日志记录和文件名
        normalized_url = image_url.split('?')[0].replace(":orig", "").replace(":large", "")
        # 从内部的原始链接提取文件名
        filename = twitter_id+'_'+normalized_url.split('/')[-1]
        if '.' not in filename:
            filename += '.jpg'
    except IndexError:
        print(f"  [失败] 无法从URL解析文件名: {image_url}")
        log_failure(f"文件名解析失败: {image_url}", image_failed_log_path)
        return

    filepath = os.path.join(download_folder, filename)

    if normalized_url in downloaded_urls_set or os.path.exists(filepath):
        print(f"  [跳过] 图片已存在: {filename}")
        # 确保即使只有文件存在，日志中也有记录
        if normalized_url not in downloaded_urls_set:
             downloaded_urls_set.add(normalized_url)
             with open(log_path, 'a', encoding='utf-8') as log_file:
                 log_file.write(normalized_url + '\n')
        return

    # 步骤 2: 尝试下载主链接
    print(f"  [下载] 准备下载: {filename}")
    response = get_with_retries(image_url, timeout=60)

    # 步骤 3: 如果主链接失败，则尝试转换URL并用原始链接重试
    if not response:
        print(f"  [警告] 主链接: {image_url} 下载失败。")
        raw_url = _transform_to_raw_url(image_url)
        if raw_url:
            print(f"  [重试] 尝试原始文件链接: {raw_url}")
            response = get_with_retries(raw_url, timeout=60)
        else:
            print(f"  [失败] 无法生成用于重试的原始链接。")

    # 步骤 4: 如果任一请求成功，则保存文件
    if response:
        try:
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(8192):
                    f.write(chunk)

            # 记录成功信息
            downloaded_urls_set.add(normalized_url)
            with open(log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(normalized_url + '\n')

            print(f"  [成功] ✓ 图片已保存: {filepath}")

        except Exception as e:
            error_msg = f"文件保存失败: {image_url} | 错误: {e}"
            print(f"  [失败] ✗ {error_msg}")
            log_failure(error_msg, image_failed_log_path)
    else:
        # 如果主链接和重试链接都失败了
        final_error_msg = f"下载失败 (所有途径均失败): {image_url}"
        print(f"  [失败] ✗ {final_error_msg}")
        log_failure(final_error_msg, image_failed_log_path)

def main(twitter_id):
    """
    主执行函数
    """
    print("===============================================")
    print("=== Wayback Machine 推特图片下载器 ===")
    print("===============================================")

    if not twitter_id:
        print("错误: 推特ID不能为空！")
        return

    user_folder, user_log_path, snapshot_failed_log_path, image_failed_log_path = setup_environment(twitter_id)
    downloaded_urls = load_downloaded_urls(user_log_path)
    print(f"已加载 {len(downloaded_urls)} 条已下载记录。")
    print(f"页面抓取/解析失败日志将记录在: {snapshot_failed_log_path}")
    print(f"图片下载失败日志将记录在: {image_failed_log_path}")

    snapshots = get_wayback_snapshots(twitter_id, snapshot_failed_log_path)
    if not snapshots:
        print(f"未能找到ID为 @{twitter_id} 的任何可用历史快照。程序退出。")
        return

    total_snapshots = len(snapshots)
    print(f"\n[步骤 2/3] 开始遍历并处理 {total_snapshots} 个历史页面...")

    new_images_downloaded_session = 0
    for i, snapshot in enumerate(snapshots):
        timestamp, original_url = snapshot
        wayback_page_url = f"https://web.archive.org/web/{timestamp}/{original_url}"

        print(f"\n--- 处理快照 {i + 1}/{total_snapshots} ---")
        image_urls_on_page = get_image_urls_from_page(wayback_page_url, twitter_id, snapshot_failed_log_path)

        if not image_urls_on_page:
            print("此页面未发现图片，或解析/抓取失败。")
            continue

        print(f"开始处理该页面的 {len(image_urls_on_page)} 个图片链接...")
        for url in image_urls_on_page:
            initial_count = len(downloaded_urls)
            download_image(twitter_id,url, user_folder, downloaded_urls, user_log_path, image_failed_log_path)
            if len(downloaded_urls) > initial_count:
                new_images_downloaded_session += 1

    print("\n===============================================")
    print(f"          [步骤 3/3] 所有任务已完成！")
    print(f"          本次运行新下载了 {new_images_downloaded_session} 张图片。")
    print(f"          所有图片均保存在: {os.path.abspath(user_folder)}")
    print(f"          失败的条目已记录在对应的日志文件中。")
    print("===============================================")


if __name__ == "__main__":
    # --- 在此处输入您想下载的推特ID,注意需要区分大小写---
    # 示例: twitter_ids_to_process = ["NASA","realDonaldTrump"]
    twitter_ids_to_process = [""]
    for name in twitter_ids_to_process:

        main(name)
