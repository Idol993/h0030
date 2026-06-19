import os
import time
import functools
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any

import requests

from .models import PublishResult, ArticleMetadata, PlatformConfig


def retry_with_backoff(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.RequestException as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                        delay *= backoff_factor
                    else:
                        raise
            raise last_exception
        return wrapper
    return decorator


def get_platform_config_from_env(platform: str) -> Optional[PlatformConfig]:
    prefix = platform.upper()

    api_key = os.environ.get(f"{prefix}_API_KEY") or os.environ.get(f"{prefix}_APP_ID")
    api_secret = os.environ.get(f"{prefix}_API_SECRET") or os.environ.get(f"{prefix}_APP_SECRET")
    access_token = os.environ.get(f"{prefix}_ACCESS_TOKEN")
    category = os.environ.get(f"{prefix}_CATEGORY")

    if not any([api_key, api_secret, access_token]):
        return None

    params = {}
    if category:
        params["category"] = category

    return PlatformConfig(
        name=platform,
        type=platform,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        params=params,
    )


class BasePublisher(ABC):
    platform: str = "base"

    def __init__(self, config: PlatformConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

    def publish(self, content: str, metadata: ArticleMetadata, draft: bool = False) -> PublishResult:
        if self.dry_run:
            return self._dry_run_publish(content, metadata, draft)
        try:
            return self._do_publish(content, metadata, draft)
        except Exception as e:
            return PublishResult(
                success=False,
                platform=self.platform,
                error=str(e),
                is_draft=draft,
            )

    def _dry_run_publish(self, content: str, metadata: ArticleMetadata, draft: bool) -> PublishResult:
        action = "保存草稿" if draft else "发布"
        preview = content[:200] + "..." if len(content) > 200 else content
        return PublishResult(
            success=True,
            platform=self.platform,
            url=f"[DRY-RUN] {action}: {metadata.title}",
            is_draft=draft,
        )

    def get_dry_run_info(self, content: str, metadata: ArticleMetadata, draft: bool) -> dict:
        return {
            "platform": self.platform,
            "title": metadata.title,
            "summary": metadata.summary,
            "tags": metadata.tags,
            "draft": draft,
            "content_preview": content[:500] + "..." if len(content) > 500 else content,
            "content_type": "html" if self.platform == "wechat" else "markdown",
        }

    @abstractmethod
    def _do_publish(self, content: str, metadata: ArticleMetadata, draft: bool = False) -> PublishResult:
        pass


class WechatPublisher(BasePublisher):
    platform = "wechat"

    BASE_URL = "https://api.weixin.qq.com/cgi-bin"

    def _get_access_token(self) -> str:
        if self.config.access_token:
            return self.config.access_token

        if not self.config.api_key or not self.config.api_secret:
            raise ValueError(
                "微信公众号凭证缺失：需要配置 WECHAT_API_KEY (APPID) 和 WECHAT_API_SECRET (APPSECRET)，"
                "或在配置文件中设置，或通过环境变量/--env 文件提供。"
            )

        url = f"{self.BASE_URL}/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.config.api_key,
            "secret": self.config.api_secret,
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "access_token" not in data:
            raise ValueError(f"获取微信 access_token 失败: {data}")
        return data["access_token"]

    @retry_with_backoff(max_retries=3)
    def _upload_image_from_url(self, image_url: str, access_token: str) -> str:
        if not access_token:
            raise ValueError(
                "微信素材上传失败：缺少有效 access_token。"
                "请检查 WECHAT_API_KEY 和 WECHAT_API_SECRET 是否正确配置。"
            )

        try:
            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"下载图片失败 {image_url}: {e}")

        url = f"{self.BASE_URL}/material/add_material"
        params = {
            "access_token": access_token,
            "type": "image",
        }

        filename = image_url.split("/")[-1] or "image.jpg"
        if "?" in filename:
            filename = filename.split("?")[0]
        if "." not in filename:
            filename += ".jpg"

        files = {
            "media": (filename, img_resp.content, img_resp.headers.get("Content-Type", "image/jpeg"))
        }

        response = requests.post(url, params=params, files=files, timeout=60)
        response.raise_for_status()
        data = response.json()

        if data.get("errcode", 0) != 0:
            raise ValueError(f"微信图片上传失败: {data}")

        return data.get("media_id", "")

    def upload_image(self, image_url: str) -> str:
        access_token = self._get_access_token()
        return self._upload_image_from_url(image_url, access_token)

    @retry_with_backoff(max_retries=3)
    def _add_draft(self, content: str, metadata: ArticleMetadata, access_token: str) -> str:
        url = f"{self.BASE_URL}/draft/add"
        params = {"access_token": access_token}

        articles = [{
            "title": metadata.title,
            "author": metadata.author or "",
            "digest": metadata.summary or "",
            "content": content,
            "thumb_media_id": metadata.cover or "",
            "content_source_url": metadata.original_url or "",
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        }]

        response = requests.post(url, params=params, json={"articles": articles}, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("errcode", 0) != 0:
            raise ValueError(f"创建微信草稿失败: {data}")
        return data.get("media_id", "")

    @retry_with_backoff(max_retries=3)
    def _publish_draft(self, media_id: str, access_token: str) -> str:
        url = f"{self.BASE_URL}/message/mass/sendall"
        params = {"access_token": access_token}

        data = {
            "filter": {
                "is_to_all": True,
            },
            "mpnews": {
                "media_id": media_id,
            },
            "msgtype": "mpnews",
            "send_ignore_reprint": 0,
        }

        response = requests.post(url, params=params, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result.get("errcode", 0) != 0:
            raise ValueError(f"微信发布失败: {result}")
        return result.get("msg_id", "")

    def _do_publish(self, content: str, metadata: ArticleMetadata, draft: bool = False) -> PublishResult:
        access_token = self._get_access_token()

        media_id = self._add_draft(content, metadata, access_token)

        if draft:
            return PublishResult(
                success=True,
                platform=self.platform,
                url=f"https://mp.weixin.qq.com/ (草稿 media_id: {media_id})",
                is_draft=True,
            )

        msg_id = self._publish_draft(media_id, access_token)

        return PublishResult(
            success=True,
            platform=self.platform,
            url=f"https://mp.weixin.qq.com/ (msg_id: {msg_id})",
            is_draft=False,
        )


class ZhihuPublisher(BasePublisher):
    platform = "zhihu"

    BASE_URL = "https://api.zhihu.com"

    def _validate_config(self) -> None:
        if not self.config.access_token:
            raise ValueError(
                "知乎凭证缺失：需要配置 ZHIHU_ACCESS_TOKEN，"
                "或在配置文件中设置，或通过环境变量/--env 文件提供。"
            )

    @retry_with_backoff(max_retries=3)
    def _do_publish(self, content: str, metadata: ArticleMetadata, draft: bool = False) -> PublishResult:
        self._validate_config()

        url = f"{self.BASE_URL}/articles"
        headers = {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "title": metadata.title,
            "content": content,
            "topics": metadata.tags,
            "is_draft": draft,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        article_id = data.get("id", "")
        article_url = f"https://zhuanlan.zhihu.com/p/{article_id}"

        return PublishResult(
            success=True,
            platform=self.platform,
            url=article_url,
            is_draft=draft,
        )


class JuejinPublisher(BasePublisher):
    platform = "juejin"

    BASE_URL = "https://api.juejin.cn"

    def _get_category_id(self) -> int:
        category_name = self.config.params.get("category", "前端")
        category_map = {
            "前端": 6809635626879549454,
            "后端": 6809637769959178254,
            "Android": 6809635626661443592,
            "iOS": 6809635626661443591,
            "人工智能": 6809637773935378440,
            "开发工具": 6809637771592990727,
            "代码人生": 6809638761295478792,
            "阅读": 6809637776263217163,
        }
        return category_map.get(category_name, 6809635626879549454)

    def _validate_config(self) -> None:
        if not self.config.access_token:
            raise ValueError(
                "掘金凭证缺失：需要配置 JUEJIN_ACCESS_TOKEN，"
                "或在配置文件中设置，或通过环境变量/--env 文件提供。"
            )

    @retry_with_backoff(max_retries=3)
    def _do_publish(self, content: str, metadata: ArticleMetadata, draft: bool = False) -> PublishResult:
        self._validate_config()

        url = f"{self.BASE_URL}/content_api/v1/article/publish"
        if draft:
            url = f"{self.BASE_URL}/content_api/v1/article_draft/create"

        headers = {
            "X-Legacy-Token": self.config.access_token,
            "Content-Type": "application/json",
        }

        category_id = self._get_category_id()
        tags = metadata.tags[:5] if metadata.tags else ["前端", "后端", "程序员"]

        payload = {
            "article": {
                "title": metadata.title,
                "content": content,
                "category_id": category_id,
                "tag_ids": [],
                "brief_content": metadata.summary or "",
            },
            "draft_id": 0,
        }

        if not draft:
            payload["article"]["tags"] = tags

        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data.get("err_no", 0) != 0:
            raise ValueError(f"掘金发布失败: {data.get('err_msg', '未知错误')}")

        article_id = data.get("data", {}).get("article_id", "")
        article_url = f"https://juejin.cn/post/{article_id}"

        return PublishResult(
            success=True,
            platform=self.platform,
            url=article_url if not draft else f"https://juejin.cn/editor/drafts/{article_id}",
            is_draft=draft,
        )


_publisher_registry = {
    "wechat": WechatPublisher,
    "zhihu": ZhihuPublisher,
    "juejin": JuejinPublisher,
}


def get_publisher(platform: str, config: PlatformConfig, dry_run: bool = False) -> BasePublisher:
    publisher_cls = _publisher_registry.get(platform)
    if not publisher_cls:
        raise ValueError(f"不支持的平台: {platform}")
    return publisher_cls(config, dry_run=dry_run)


def get_supported_platforms() -> list:
    return list(_publisher_registry.keys())
