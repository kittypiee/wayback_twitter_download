## 说明

- 根据已知id爬取Wayback Machine中存档的历史推特中的图片；
- 仅爬取该id所发图片，自动去重；
- 自动解析存档页面，下载失败图片自动记录。
- 需要保证科学上网环境，可在colab中运行

## 快速开始

### 拉取&&安装

```bash
git clone https://github.com/kittypiee/wayback_twitter_download.git
cd wayback_twitter_download
pip install -r requirements.txt
```

## 运行程序
- 在main.py中添加爬取id
```bash
python main.py
```

## 在Colab中直接运行

[![Open In colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/1bBaM35CL08QV9k6uXH-FNT4qwprN4kzA?usp=sharing)

## 如果对你有所帮助，感谢打赏

![我的照片](./wecha.png)
