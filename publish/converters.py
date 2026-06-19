import re
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List
from pathlib import Path

import markdown
from bs4 import BeautifulSoup

from .models import ArticleMetadata


class BaseConverter(ABC):
    platform: str = "base"

    def convert(self, md_text: str, metadata: Optional[ArticleMetadata] = None) -> Tuple[str, ArticleMetadata]:
        if metadata is None:
            metadata = ArticleMetadata()
        metadata = self._extract_metadata(md_text, metadata)
        content = self.transform_content(md_text, metadata)
        return content, metadata

    def _extract_metadata(self, md_text: str, metadata: ArticleMetadata) -> ArticleMetadata:
        lines = md_text.split("\n")
        in_frontmatter = False
        frontmatter_lines: List[str] = []

        if lines and lines[0].strip() == "---":
            in_frontmatter = True
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    in_frontmatter = False
                    break
                frontmatter_lines.append(lines[i])

        for line in frontmatter_lines:
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "title":
                    metadata.title = value
                elif key == "summary" or key == "description":
                    metadata.summary = value
                elif key == "tags":
                    tags = [t.strip() for t in value.split(",") if t.strip()]
                    for tag in tags:
                        if tag not in metadata.tags:
                            metadata.tags.append(tag)
                elif key == "cover" or key == "image":
                    metadata.cover = value
                elif key == "author":
                    metadata.author = value

        if not metadata.title:
            for line in lines:
                if line.startswith("# "):
                    metadata.title = line[2:].strip()
                    break

        if not metadata.summary:
            plain_text = re.sub(r"[#*`>\[\]()!_\-]", "", md_text)
            plain_text = re.sub(r"\n+", " ", plain_text).strip()
            metadata.summary = plain_text[:200]

        return metadata

    @abstractmethod
    def transform_content(self, md_text: str, metadata: ArticleMetadata) -> str:
        pass

    def _remove_frontmatter(self, md_text: str) -> str:
        lines = md_text.split("\n")
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    return "\n".join(lines[i + 1:])
        return md_text


class WechatConverter(BaseConverter):
    platform = "wechat"

    def __init__(self, upload_image_func=None):
        self.upload_image_func = upload_image_func
        self.title_max_length = 64
        self.summary_max_length = 200

    def transform_content(self, md_text: str, metadata: ArticleMetadata) -> str:
        md_text = self._remove_frontmatter(md_text)

        if len(metadata.title) > self.title_max_length:
            metadata.title = metadata.title[: self.title_max_length - 3] + "..."

        if len(metadata.summary) > self.summary_max_length:
            metadata.summary = metadata.summary[: self.summary_max_length - 3] + "..."

        html = markdown.markdown(
            md_text,
            extensions=["extra", "codehilite", "tables"],
        )

        soup = BeautifulSoup(html, "html.parser")

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if self.upload_image_func and src and not src.startswith("data:"):
                try:
                    wechat_url = self.upload_image_func(src)
                    img["data-src"] = wechat_url
                    if "src" in img.attrs:
                        del img["src"]
                except Exception:
                    img["data-src"] = src
                    if "src" in img.attrs:
                        del img["src"]
            else:
                img["data-src"] = src
                if "src" in img.attrs:
                    del img["src"]

        for p in soup.find_all("p"):
            p["style"] = "margin-bottom: 1em; line-height: 1.8;"

        for h1 in soup.find_all("h1"):
            h1["style"] = "font-size: 24px; font-weight: bold; margin: 20px 0 10px;"

        for h2 in soup.find_all("h2"):
            h2["style"] = "font-size: 20px; font-weight: bold; margin: 18px 0 8px; border-bottom: 2px solid #eee; padding-bottom: 6px;"

        for h3 in soup.find_all("h3"):
            h3["style"] = "font-size: 17px; font-weight: bold; margin: 16px 0 6px;"

        for blockquote in soup.find_all("blockquote"):
            blockquote["style"] = "border-left: 4px solid #ddd; padding-left: 16px; color: #666; margin: 16px 0;"

        for code in soup.find_all("code"):
            if code.parent.name != "pre":
                code["style"] = "background: #f5f5f5; padding: 2px 6px; border-radius: 4px; font-size: 90%;"

        for pre in soup.find_all("pre"):
            pre["style"] = "background: #282c34; color: #abb2bf; padding: 16px; border-radius: 8px; overflow-x: auto;"

        return str(soup)


class ZhihuConverter(BaseConverter):
    platform = "zhihu"

    def transform_content(self, md_text: str, metadata: ArticleMetadata) -> str:
        md_text = self._remove_frontmatter(md_text)

        md_text = self._remove_unsupported_syntax(md_text)
        md_text = self._transform_tags(md_text, metadata)

        return md_text

    def _remove_unsupported_syntax(self, md_text: str) -> str:
        md_text = re.sub(r"~~(.*?)~~", r"\1", md_text)

        md_text = re.sub(r"\^(\w+)", "", md_text)

        md_text = re.sub(r":::.*?\n", "", md_text)
        md_text = re.sub(r":::", "", md_text)

        md_text = re.sub(r"\[toc\]", "", md_text, flags=re.IGNORECASE)

        return md_text

    def _transform_tags(self, md_text: str, metadata: ArticleMetadata) -> str:
        tag_pattern = r"#([^\s#]+)(?=\s|$)"
        md_text = re.sub(tag_pattern, r"#\1#", md_text)

        return md_text


class JuejinConverter(BaseConverter):
    platform = "juejin"

    min_tags = 3

    def transform_content(self, md_text: str, metadata: ArticleMetadata) -> str:
        md_text = self._remove_frontmatter(md_text)

        if len(metadata.tags) < self.min_tags:
            default_tags = ["前端", "后端", "程序员"]
            for tag in default_tags:
                if tag not in metadata.tags:
                    metadata.tags.append(tag)
                    if len(metadata.tags) >= self.min_tags:
                        break

        md_text = self._append_footer(md_text, metadata)

        return md_text

    def _append_footer(self, md_text: str, metadata: ArticleMetadata) -> str:
        footer = f"""

---

> 🌟 欢迎关注我的账号，第一时间获取更多技术干货！
> 
> 👍 如果觉得文章对你有帮助，别忘了点赞收藏支持一下～
> 
> 💬 有任何问题欢迎在评论区交流

"""
        return md_text + footer


_converter_registry = {
    "wechat": WechatConverter,
    "zhihu": ZhihuConverter,
    "juejin": JuejinConverter,
}


def get_converter(platform: str, **kwargs) -> BaseConverter:
    converter_cls = _converter_registry.get(platform)
    if not converter_cls:
        raise ValueError(f"不支持的平台: {platform}")
    return converter_cls(**kwargs)


def get_supported_platforms() -> List[str]:
    return list(_converter_registry.keys())
