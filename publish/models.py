from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class PublishResult(BaseModel):
    success: bool
    platform: str
    url: Optional[str] = None
    error: Optional[str] = None
    is_draft: bool = False

    def __str__(self) -> str:
        if self.success:
            status = "草稿" if self.is_draft else "已发布"
            return f"[{self.platform}] {status}: {self.url}"
        return f"[{self.platform}] 失败: {self.error}"


class ArticleMetadata(BaseModel):
    title: str = ""
    summary: str = ""
    cover: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    author: str = ""
    original_url: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class PlatformConfig(BaseModel):
    name: str
    type: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    access_token: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
