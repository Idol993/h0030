import os
import sys
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from datetime import datetime

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown

from .converters import get_converter, get_supported_platforms, find_external_images_in_html, replace_external_images_in_html
from .publishers import get_publisher, get_platform_config_from_env
from .config import load_config, add_platform, remove_platform, list_platforms, get_platform
from .models import ArticleMetadata, PlatformConfig

console = Console()


def _read_markdown(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]错误: 文件不存在: {file_path}[/red]")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _resolve_platform_config(platform: str, configs: Dict[str, PlatformConfig]) -> Optional[PlatformConfig]:
    for name, cfg in configs.items():
        if name == platform or cfg.type == platform:
            return cfg
    env_config = get_platform_config_from_env(platform)
    if env_config:
        return env_config
    return None


def _read_dist_file(dist_dir: Path, file_stem: str, platform: str) -> Optional[Tuple[str, ArticleMetadata]]:
    ext = ".html" if platform == "wechat" else ".md"
    content_file = dist_dir / f"{file_stem}.{platform}{ext}"
    meta_file = dist_dir / f"{file_stem}.{platform}.json"

    if not content_file.exists():
        return None

    content = content_file.read_text(encoding="utf-8")
    metadata = ArticleMetadata()

    if meta_file.exists():
        try:
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
            metadata = ArticleMetadata(**meta_data)
        except Exception:
            pass

    return content, metadata


def _print_dry_run_preview(info: dict) -> None:
    platform = info["platform"]
    status = "[blue]草稿[/blue]" if info["draft"] else "[green]正式发布[/green]"
    content_type = info["content_type"]

    title = Panel(
        Text(info["title"] or "(无标题)", style="bold white"),
        title=f"[{platform.upper()}] 内容预览",
        subtitle=f"状态: {status} | 格式: {content_type.upper()}",
        border_style="cyan",
    )
    console.print(title)

    meta_table = Table(show_header=False, border_style="dim")
    meta_table.add_column("属性", style="cyan", width=8)
    meta_table.add_column("内容", style="white")
    meta_table.add_row("摘要", info["summary"] or "(无摘要)")
    if info["tags"]:
        meta_table.add_row("标签", ", ".join(info["tags"]))
    console.print(meta_table)

    console.print(Text("正文预览:", style="bold yellow"))
    content_preview = info["content_preview"]

    if content_type == "markdown":
        console.print(Panel(Markdown(content_preview), border_style="dim"))
    else:
        preview_text = Text(content_preview[:300] + "..." if len(content_preview) > 300 else content_preview)
        console.print(Panel(preview_text, border_style="dim"))

    console.print()


def _build_report_section(info: dict, source: str) -> str:
    lines = []
    lines.append(f"## {info['platform'].upper()}")
    lines.append("")
    lines.append(f"- **标题**: {info['title'] or '(无标题)'}")
    lines.append(f"- **摘要**: {info['summary'] or '(无摘要)'}")
    lines.append(f"- **标签**: {', '.join(info['tags']) if info['tags'] else '(无)'}")
    lines.append(f"- **状态**: {'草稿' if info['draft'] else '正式发布'}")
    lines.append(f"- **格式**: {info['content_type'].upper()}")
    lines.append(f"- **来源**: `{source}`")
    if info.get("diffs"):
        diffs = "; ".join(info["diffs"])
        lines.append(f"- **差异**: {diffs}")
    lines.append("")
    lines.append("### 正文预览")
    lines.append("")
    if info["content_type"] == "markdown":
        lines.append(info["content_preview"])
    else:
        lines.append("```html")
        lines.append(info["content_preview"][:500])
        lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _do_publish(
    platforms: Optional[str],
    all_platforms: bool,
    draft: bool,
    dry_run: bool,
    file: Optional[str],
    dist_dir: str,
    report: Optional[str] = None,
) -> None:
    configs = load_config()

    if all_platforms:
        target_platforms = []
        for p in get_supported_platforms():
            if _resolve_platform_config(p, configs):
                target_platforms.append(p)
        if not target_platforms:
            target_platforms = list(configs.keys())
    elif platforms:
        target_platforms = [p.strip() for p in platforms.split(",")]
    else:
        console.print("[yellow]请指定发布平台 --platforms 或使用 --all[/yellow]")
        sys.exit(1)

    if not target_platforms:
        console.print("[red]没有可用的平台配置。请通过以下任一方式配置：[/red]")
        console.print("  1. 使用 'publish platforms add' 添加平台配置")
        console.print("  2. 在 .env 文件或环境变量中设置平台密钥 (如 ZHIHU_ACCESS_TOKEN)")
        console.print("  3. 使用 --env 指定包含密钥的环境变量文件")
        sys.exit(1)

    md_text = None
    file_stem = None

    if file:
        md_text = _read_markdown(file)
        file_stem = Path(file).stem
    else:
        dist_path = Path(dist_dir)
        json_files = list(dist_path.glob("*.json"))
        if json_files:
            file_stem = json_files[0].stem.split(".")[0]
        else:
            md_files = list(dist_path.glob("*.md"))
            if md_files:
                file_stem = md_files[0].stem

        if not file_stem:
            console.print(f"[red]无法在 {dist_dir} 中找到已转换的文章文件。[/red]")
            console.print("请先执行 'publish convert article.md --all' 生成各平台格式，")
            console.print("或使用 --file 指定 Markdown 原文路径。")
            sys.exit(1)

    if dry_run:
        console.print(Panel("[yellow] DRY RUN 模式 - 不会实际调用平台API [/yellow]", expand=False))

    if draft:
        console.print(Panel("[blue] DRAFT 模式 - 仅保存草稿不正式发布 [/blue]", expand=False))

    console.print(f"\n目标平台: {', '.join(target_platforms)}")
    console.print(f"文章标识: {file_stem}\n")

    results = []
    report_sections = []
    report_infos = []
    original_metadata = ArticleMetadata()

    if md_text:
        base_converter = get_converter("zhihu")
        _, original_metadata = base_converter.convert(md_text, ArticleMetadata())

    for platform in target_platforms:
        config = _resolve_platform_config(platform, configs)
        if not config:
            error_msg = (
                f"平台 '{platform}' 未配置凭证。\n"
                f"请设置 {platform.upper()}_ACCESS_TOKEN (或 {platform.upper()}_API_KEY + {platform.upper()}_API_SECRET) \n"
                f"环境变量，或使用 'publish platforms add' 添加配置。"
            )
            console.print(f"[red]✗[/red] [{platform}] 配置缺失")
            console.print(f"    {error_msg}")
            result = {
                "success": False,
                "platform": platform,
                "error": error_msg,
            }
            results.append(result)
            if dry_run and report:
                info = {
                    "platform": platform,
                    "title": "",
                    "summary": "",
                    "tags": [],
                    "draft": draft,
                    "content_preview": "",
                    "content_type": "html" if platform == "wechat" else "markdown",
                    "source": "N/A (配置缺失)",
                    "dist_file": "",
                    "diffs": [],
                    "will_publish": False,
                    "error": error_msg,
                }
                report_infos.append(info)
                report_sections.append(_build_report_section(info, info["source"]))
            continue

        content = None
        metadata = ArticleMetadata()
        dist_file_path = ""
        source = ""
        diffs = []

        if md_text:
            source = str(file)
            try:
                metadata = ArticleMetadata()
                converter_kwargs = {}

                if platform == "wechat":
                    if not dry_run:
                        publisher = get_publisher(platform, config, dry_run=False)
                        converter_kwargs["upload_image_func"] = publisher.upload_image
                    else:
                        converter_kwargs["fail_on_image_error"] = False

                converter = get_converter(platform, **converter_kwargs)
                content, meta = converter.convert(md_text, metadata)
                metadata = meta

                ext = ".html" if platform == "wechat" else ".md"
                dist_file_path = f"{file_stem}.{platform}{ext}"

                if original_metadata.title and meta.title != original_metadata.title:
                    diffs.append(f"标题被截断 ({len(original_metadata.title)} → {len(meta.title)} 字)")
                if platform == "juejin" and original_metadata.tags:
                    added_tags = [t for t in meta.tags if t not in original_metadata.tags]
                    if added_tags:
                        diffs.append(f"自动补足标签: {', '.join(added_tags)}")
                if platform == "wechat":
                    external_imgs = find_external_images_in_html(content)
                    if external_imgs:
                        diffs.append(f"还有 {len(external_imgs)} 张外链图片未上传素材库")

            except Exception as e:
                console.print(f"[red]✗[/red] [{platform}] 转换失败: {e}")
                result = {
                    "success": False,
                    "platform": platform,
                    "error": str(e),
                }
                results.append(result)
                if dry_run and report:
                    info = {
                        "platform": platform,
                        "title": "",
                        "summary": "",
                        "tags": [],
                        "draft": draft,
                        "content_preview": "",
                        "content_type": "html" if platform == "wechat" else "markdown",
                        "source": source,
                        "dist_file": dist_file_path,
                        "diffs": diffs,
                        "will_publish": False,
                        "error": str(e),
                    }
                    report_infos.append(info)
                continue
        else:
            ext = ".html" if platform == "wechat" else ".md"
            source = str(Path(dist_dir) / f"{file_stem}.{platform}{ext}")
            dist_result = _read_dist_file(Path(dist_dir), file_stem, platform)
            if dist_result is None:
                ext = ".html" if platform == "wechat" else ".md"
                expected_file = f"{file_stem}.{platform}{ext}"
                error_msg = f"dist 目录中缺少该平台文件: {expected_file}"
                console.print(f"[yellow]⚠[/yellow] [{platform}] {error_msg}，跳过")
                result = {
                    "success": False,
                    "platform": platform,
                    "error": error_msg,
                }
                results.append(result)
                if dry_run and report:
                    info = {
                        "platform": platform,
                        "title": "",
                        "summary": "",
                        "tags": [],
                        "draft": draft,
                        "content_preview": "",
                        "content_type": "html" if platform == "wechat" else "markdown",
                        "source": source,
                        "dist_file": expected_file,
                        "diffs": diffs,
                        "will_publish": False,
                        "error": error_msg,
                    }
                    report_infos.append(info)
                    report_sections.append(_build_report_section(info, source))
                continue
            content, metadata = dist_result
            ext = ".html" if platform == "wechat" else ".md"
            dist_file_path = f"{file_stem}.{platform}{ext}"

            if platform == "juejin" and len(metadata.tags) >= 3:
                default_tags = ["前端", "后端", "程序员"]
                added_tags = [t for t in default_tags if t in metadata.tags and t not in original_metadata.tags]
                if added_tags and original_metadata.tags:
                    diffs.append(f"包含自动补足的标签: {', '.join(added_tags)}")
            if platform == "wechat":
                external_imgs = find_external_images_in_html(content)
                if external_imgs:
                    diffs.append(f"还有 {len(external_imgs)} 张外链图片未上传素材库")

            if platform == "wechat" and not dry_run:
                external_imgs = find_external_images_in_html(content)
                if external_imgs:
                    console.print(f"    [yellow]↑[/yellow] [{platform}] 发现 {len(external_imgs)} 张外链图片，尝试上传到微信素材库...")
                    try:
                        publisher = get_publisher(platform, config, dry_run=False)
                        content, failed_uploads = replace_external_images_in_html(content, publisher.upload_image)
                        if failed_uploads:
                            raise RuntimeError(
                                f"微信图片上传失败 ({len(failed_uploads)} 张): {', '.join(failed_uploads[:3])}...\n"
                                "请检查 WECHAT_API_KEY 和 WECHAT_API_SECRET 是否正确，"
                                "或使用 --dry-run 跳过图片上传。"
                            )
                        console.print(f"    [green]✓[/green] [{platform}] {len(external_imgs)} 张图片已上传到素材库")
                        dist_content_file = Path(dist_dir) / f"{file_stem}.{platform}.html"
                        dist_content_file.write_text(content, encoding="utf-8")
                    except RuntimeError as e:
                        console.print(f"[red]✗[/red] [{platform}] 图片上传失败: {e}")
                        result = {
                            "success": False,
                            "platform": platform,
                            "error": str(e),
                        }
                        results.append(result)
                        continue
                    except Exception as e:
                        error_msg = (
                            f"微信图片上传失败: {e}\n"
                            "请检查 WECHAT_API_KEY 和 WECHAT_API_SECRET 是否正确，"
                            "或使用 --dry-run 跳过图片上传。"
                        )
                        console.print(f"[red]✗[/red] [{platform}] {error_msg}")
                        result = {
                            "success": False,
                            "platform": platform,
                            "error": error_msg,
                        }
                        results.append(result)
                        continue

        if dry_run:
            publisher = get_publisher(platform, config, dry_run=True)
            info = publisher.get_dry_run_info(content, metadata, draft)
            info["dist_file"] = dist_file_path
            info["source"] = source
            info["diffs"] = diffs
            info["will_publish"] = True
            info["error"] = None
            _print_dry_run_preview(info)
            result = publisher.publish(content, metadata, draft=draft)

            if report:
                report_infos.append(info)
                report_sections.append(_build_report_section(info, source))
        else:
            try:
                publisher = get_publisher(platform, config, dry_run=False)
                result = publisher.publish(content, metadata, draft=draft)
            except Exception as e:
                console.print(f"[red]✗[/red] [{platform}] 异常: {e}")
                result = {
                    "success": False,
                    "platform": platform,
                    "error": str(e),
                }
                results.append(result)
                continue

        if result.success:
            status = "草稿" if result.is_draft else "已发布"
            console.print(f"[green]✓[/green] [{platform}] {status}: {result.url}")
        else:
            console.print(f"[red]✗[/red] [{platform}] 失败: {result.error}")

        results.append(result.model_dump())

    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    console.print()
    console.print(f"[bold]发布结果:[/bold] 成功 [green]{success_count}[/green] 个，失败 [red]{fail_count}[/red] 个")

    if report and report_sections:
        _write_report(report, file_stem, report_sections, draft, dist_dir, report_infos, source if md_text else None)
        console.print(f"\n[bold green]报告已保存: {report}[/bold green]")
        json_path = Path(report).with_suffix(".json")
        console.print(f"[bold green]JSON 报告已保存: {json_path}[/bold green]")

    if fail_count > 0 and success_count == 0:
        sys.exit(1)


def _write_report(
    report_path: str,
    file_stem: str,
    sections: List[str],
    draft: bool,
    dist_dir: str,
    infos: List[dict],
    source_file: Optional[str] = None,
) -> None:
    lines = []
    lines.append(f"# 发布预览报告 - {file_stem}")
    lines.append("")
    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **模式**: {'草稿' if draft else '正式发布'}")
    if source_file:
        lines.append(f"- **原文文件**: `{source_file}`")
    else:
        lines.append(f"- **dist 目录**: `{dist_dir}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    has_diffs = any(info.get("diffs") for info in infos)
    if has_diffs:
        lines.append("## 平台差异汇总")
        lines.append("")
        lines.append("| 平台 | 标题 | 标签数 | 状态 | 差异说明 |")
        lines.append("|------|------|--------|------|----------|")
        for info in infos:
            title = info.get("title", "") or "(无)"
            tag_count = len(info.get("tags", []))
            status = []
            if info.get("error"):
                status.append("❌ 失败")
            elif info.get("will_publish"):
                status.append("✅ 可发布")
            else:
                status.append("⚠️ 跳过")
            if info.get("draft"):
                status.append("草稿")
            diffs = "; ".join(info.get("diffs", [])) if info.get("diffs") else "-"
            lines.append(f"| {info['platform'].upper()} | {title} | {tag_count} | {' '.join(status)} | {diffs} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend(sections)

    Path(report_path).write_text("\n".join(lines), encoding="utf-8")

    json_path = Path(report_path).with_suffix(".json")
    json_data = {
        "generated_at": datetime.now().isoformat(),
        "article": file_stem,
        "mode": "draft" if draft else "publish",
        "source": source_file if source_file else dist_dir,
        "platforms": [],
    }
    for info in infos:
        json_data["platforms"].append({
            "platform": info["platform"],
            "title": info.get("title", ""),
            "summary": info.get("summary", ""),
            "tags": info.get("tags", []),
            "draft": info.get("draft", False),
            "content_type": info.get("content_type", ""),
            "content_preview": info.get("content_preview", ""),
            "source": info.get("source", ""),
            "dist_file": info.get("dist_file", ""),
            "diffs": info.get("diffs", []),
            "will_publish": info.get("will_publish", False),
            "error": info.get("error"),
        })
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")


def _do_doctor(env_file: Optional[str], dist_dir: str, file: Optional[str]) -> None:
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    configs = load_config()

    if file:
        check_mode = "原文模式"
    else:
        check_mode = "dist 目录模式"

    console.print(Panel(f"[bold]Publish Doctor - 发布前检查 ({check_mode})[/bold]", border_style="green"))

    console.print("\n[bold cyan]1. 环境变量文件[/bold cyan]")
    env_path = Path(env_file) if env_file else Path.cwd() / ".env"
    if env_path.exists():
        console.print(f"  [green]✓[/green] 找到环境变量文件: {env_path}")
    else:
        console.print(f"  [yellow]⚠[/yellow] 未找到环境变量文件: {env_path}")
        console.print("    建议: 创建 .env 文件或使用 --env 指定路径")

    console.print("\n[bold cyan]2. 平台凭证配置[/bold cyan]")
    platform_table = Table(show_header=True)
    platform_table.add_column("平台", style="cyan")
    platform_table.add_column("配置来源", style="yellow")
    platform_table.add_column("凭证状态", style="magenta")
    platform_table.add_column("说明", style="white")

    for p in get_supported_platforms():
        cfg = _resolve_platform_config(p, configs)
        if cfg:
            source = "配置文件" if p in configs else "环境变量"
            if p == "wechat":
                has_cred = "✓" if (cfg.api_key and cfg.api_secret) or cfg.access_token else "✗"
                note = "需要 API_KEY + API_SECRET" if has_cred == "✗" else "凭证完整"
            else:
                has_cred = "✓" if cfg.access_token else "✗"
                note = f"需要 {p.upper()}_ACCESS_TOKEN" if has_cred == "✗" else "凭证完整"
            platform_table.add_row(p, source, has_cred, note)
        else:
            platform_table.add_row(p, "-", "✗", f"设置 {p.upper()}_ACCESS_TOKEN 或 platforms add")

    console.print(platform_table)

    original_meta = None
    if file:
        console.print("\n[bold cyan]3. 原文元信息解析[/bold cyan]")
        md_text = _read_markdown(file)
        base_converter = get_converter("zhihu")
        _, original_meta = base_converter.convert(md_text, ArticleMetadata())

        console.print(f"  原文文件: [bold]{file}[/bold]")
        console.print(f"  文章标题: {original_meta.title or '(无)'}")
        if original_meta.tags:
            console.print(f"  原文标签: {', '.join(original_meta.tags)}")
        if original_meta.summary:
            s = original_meta.summary[:60] + "..." if len(original_meta.summary) > 60 else original_meta.summary
            console.print(f"  原文摘要: {s}")

        console.print("\n[bold cyan]4. dist 文件生成状态[/bold cyan]")
        file_stem = Path(file).stem
        dist_path = Path(dist_dir)

        dist_state_table = Table(show_header=True)
        dist_state_table.add_column("平台", style="cyan")
        dist_state_table.add_column("期望文件", style="green")
        dist_state_table.add_column("状态", style="magenta")
        dist_state_table.add_column("下一步", style="white")

        for p in get_supported_platforms():
            ext = ".html" if p == "wechat" else ".md"
            expected_file = f"{file_stem}.{p}{ext}"
            content_file = dist_path / expected_file
            meta_file = dist_path / f"{file_stem}.{p}.json"

            if content_file.exists():
                size = content_file.stat().st_size
                status = f"✓ 已生成 ({size} bytes)"
                if not meta_file.exists():
                    status += " (元信息缺失)"
                next_step = "-"
            else:
                status = "✗ 未生成"
                next_step = f"publish convert {file} --target {p}"
            dist_state_table.add_row(p, expected_file, status, next_step)

        console.print(dist_state_table)

        console.print("\n[bold cyan]5. 平台转换差异预测[/bold cyan]")
        diff_table = Table(show_header=True)
        diff_table.add_column("平台", style="cyan")
        diff_table.add_column("差异项", style="yellow")
        diff_table.add_column("说明", style="white")

        for p in get_supported_platforms():
            diffs = []
            if p == "wechat" and original_meta.title and len(original_meta.title) > 64:
                diffs.append(f"标题将被截断 ({len(original_meta.title)} → 64 字)")
            if p == "juejin" and len(original_meta.tags) < 3:
                need = 3 - len(original_meta.tags)
                diffs.append(f"将自动补足 {need} 个标签")
            if p == "wechat":
                imgs = re.findall(r'!\[.*?\]\((https?://.*?)\)', md_text)
                if imgs:
                    diffs.append(f"有 {len(imgs)} 张图片需上传素材库")

            if diffs:
                for d in diffs:
                    diff_table.add_row(p, d, "")
            else:
                diff_table.add_row(p, "无", "与原文一致")

        console.print(diff_table)

        console.print("\n[bold cyan]6. 发布就绪总结[/bold cyan]")
        for p in get_supported_platforms():
            cfg = _resolve_platform_config(p, configs)
            ext = ".html" if p == "wechat" else ".md"
            dist_file_exists = (dist_path / f"{file_stem}.{p}{ext}").exists()

            issues = []
            if not cfg:
                issues.append("凭证缺失")
            elif p == "wechat" and not ((cfg.api_key and cfg.api_secret) or cfg.access_token):
                issues.append("凭证不完整")
            if not dist_file_exists:
                issues.append("dist 文件未生成")

            if not issues:
                console.print(f"  [green]✓ {p}[/green]: 可发布")
            elif "凭证缺失" in issues or "凭证不完整" in issues:
                console.print(f"  [red]✗ {p}[/red]: 将失败 - {'; '.join(issues)}")
                console.print(f"    → 设置 {p.upper()}_ACCESS_TOKEN 环境变量或执行 publish platforms add")
            elif "dist 文件未生成" in issues:
                console.print(f"  [yellow]⚠ {p}[/yellow]: 将跳过 - {'; '.join(issues)}")
                console.print(f"    → 先执行 publish convert {file} --target {p}")
            else:
                console.print(f"  [yellow]⚠ {p}[/yellow]: {'; '.join(issues)}")
                console.print(f"    → 检查微信凭证后重试，或使用 --dry-run 预览")
    else:
        console.print("\n[bold cyan]3. dist 目录文件[/bold cyan]")
        dist_path = Path(dist_dir)
        file_stem = None
        if not dist_path.exists():
            console.print(f"  [red]✗[/red] dist 目录不存在: {dist_dir}")
            console.print("    建议: 先执行 'publish convert article.md --all'")
        else:
            json_files = list(dist_path.glob("*.json"))
            if json_files:
                file_stem = json_files[0].stem.split(".")[0]
            else:
                md_files = list(dist_path.glob("*.md"))
                file_stem = md_files[0].stem if md_files else None

            if file_stem:
                dist_table = Table(show_header=True)
                dist_table.add_column("平台", style="cyan")
                dist_table.add_column("文件", style="green")
                dist_table.add_column("状态", style="magenta")

                for p in get_supported_platforms():
                    ext = ".html" if p == "wechat" else ".md"
                    content_file = dist_path / f"{file_stem}.{p}{ext}"
                    meta_file = dist_path / f"{file_stem}.{p}.json"

                    if content_file.exists():
                        size = content_file.stat().st_size
                        status = f"✓ ({size} bytes)"
                        if not meta_file.exists():
                            status += " (元信息缺失)"
                    else:
                        status = "✗ 缺失"
                    dist_table.add_row(p, content_file.name, status)

                console.print(dist_table)

                console.print("\n[bold cyan]4. 文章元信息[/bold cyan]")
                for p in get_supported_platforms():
                    meta_file = dist_path / f"{file_stem}.{p}.json"
                    if meta_file.exists():
                        try:
                            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
                            meta = ArticleMetadata(**meta_data)
                            console.print(f"  [{p}] 标题: {meta.title or '(无)'}")
                            if meta.tags:
                                console.print(f"         标签: {', '.join(meta.tags)}")
                            if meta.summary:
                                s = meta.summary[:60] + "..." if len(meta.summary) > 60 else meta.summary
                                console.print(f"         摘要: {s}")
                            if p == "juejin" and len(meta.tags) < 3:
                                console.print(f"         [yellow]⚠ 标签不足3个，发布时会自动补充[/yellow]")
                        except Exception as e:
                            console.print(f"  [{p}] [red]元信息解析失败: {e}[/red]")
            else:
                console.print(f"  [yellow]⚠[/yellow] dist 目录为空，没有找到已转换的文件")
                console.print("    建议: 先执行 'publish convert article.md --all'")

        console.print("\n[bold cyan]5. 微信图片素材状态[/bold cyan]")
        wechat_cfg = _resolve_platform_config("wechat", configs)
        wechat_html = None
        if file_stem and dist_path.exists():
            wechat_file = dist_path / f"{file_stem}.wechat.html"
            if wechat_file.exists():
                wechat_html = wechat_file.read_text(encoding="utf-8")

        if wechat_html:
            external_imgs = find_external_images_in_html(wechat_html)
            if not external_imgs:
                console.print("  [green]✓[/green] 无外链图片，所有图片已上传素材库")
            else:
                console.print(f"  [yellow]⚠[/yellow] 发现 {len(external_imgs)} 张外链图片尚未上传到素材库:")
                for url in external_imgs:
                    short_url = url[:60] + "..." if len(url) > 60 else url
                    console.print(f"    - {short_url}")

                if wechat_cfg and ((wechat_cfg.api_key and wechat_cfg.api_secret) or wechat_cfg.access_token):
                    console.print("  [green]✓[/green] 微信凭证已配置，发布时会自动上传这些图片")
                else:
                    console.print("  [red]✗[/red] 微信凭证缺失，图片上传将失败")
                    console.print("    建议: 设置 WECHAT_API_KEY 和 WECHAT_API_SECRET 环境变量")
        else:
            if wechat_cfg:
                console.print("  [yellow]⚠[/yellow] 未找到微信 HTML 文件，无法检查图片状态")
            else:
                console.print("  - (无需检查，微信未配置或无 HTML 文件)")

        console.print("\n[bold cyan]6. 发布就绪总结[/bold cyan]")
        for p in get_supported_platforms():
            cfg = _resolve_platform_config(p, configs)
            ext = ".html" if p == "wechat" else ".md"
            dist_file_exists = False
            if file_stem:
                dist_file_exists = (dist_path / f"{file_stem}.{p}{ext}").exists()

            issues = []
            if not cfg:
                issues.append("凭证缺失")
            elif p == "wechat" and not ((cfg.api_key and cfg.api_secret) or cfg.access_token):
                issues.append("凭证不完整")
            if not dist_file_exists:
                issues.append("dist 文件缺失")

            if p == "wechat" and wechat_html and find_external_images_in_html(wechat_html):
                if not cfg or not ((cfg.api_key and cfg.api_secret) or cfg.access_token):
                    issues.append("外链图片无法上传")

            if not issues:
                console.print(f"  [green]✓ {p}[/green]: 可发布")
            elif "凭证缺失" in issues or "凭证不完整" in issues:
                console.print(f"  [red]✗ {p}[/red]: 将失败 - {'; '.join(issues)}")
                console.print(f"    → 设置 {p.upper()}_ACCESS_TOKEN 环境变量或执行 publish platforms add")
            elif "dist 文件缺失" in issues:
                console.print(f"  [yellow]⚠ {p}[/yellow]: 将跳过 - {'; '.join(issues)}")
                console.print(f"    → 先执行 publish convert --target {p}")
            else:
                console.print(f"  [yellow]⚠ {p}[/yellow]: {'; '.join(issues)}")
                console.print(f"    → 检查微信凭证后重试，或使用 --dry-run 预览")

    console.print()


_publish_options = [
    click.option("--platforms", "-p", default=None, help="发布平台，逗号分隔 (wechat,zhihu,juejin)"),
    click.option("--all", "-a", "all_platforms", is_flag=True, help="发布所有已配置的平台"),
    click.option("--draft", is_flag=True, help="草稿模式，只保存草稿不正式发布"),
    click.option("--dry-run", is_flag=True, help="试运行模式，只打印预览不实际调用API"),
    click.option("--file", "-f", type=click.Path(exists=True), help="Markdown原文文件路径"),
    click.option("--dist-dir", default="./dist", help="dist目录路径，默认 ./dist"),
    click.option("--report", default=None, help="dry-run 报告输出文件路径 (如 publish-report.md)"),
]


def add_publish_options(func):
    for option in reversed(_publish_options):
        func = option(func)
    return func


class AppGroup(click.Group):
    def invoke(self, ctx):
        args = ctx.args
        if args and args[0] in self.commands:
            return super().invoke(ctx)

        has_platforms = ctx.params.get("platforms") is not None
        has_file = ctx.params.get("file") is not None
        has_draft = ctx.params.get("draft")
        has_dry_run = ctx.params.get("dry_run")
        has_all = ctx.params.get("all_platforms")
        has_report = ctx.params.get("report") is not None

        if has_platforms or has_all or has_file or has_draft or has_dry_run or has_report:
            env = ctx.params.get("env")
            if env:
                load_dotenv(env)
            else:
                load_dotenv()

            return _do_publish(
                platforms=ctx.params.get("platforms"),
                all_platforms=ctx.params.get("all_platforms"),
                draft=ctx.params.get("draft"),
                dry_run=ctx.params.get("dry_run"),
                file=ctx.params.get("file"),
                dist_dir=ctx.params.get("dist_dir"),
                report=ctx.params.get("report"),
            )

        if not args and not any([has_platforms, has_all, has_file, has_draft, has_dry_run]):
            click.echo(ctx.get_help())
            return

        return super().invoke(ctx)


@click.group(cls=AppGroup, invoke_without_command=True)
@click.option("--env", type=click.Path(exists=True), help="环境变量文件路径")
@click.option("--platforms", "-p", default=None, help="发布平台，逗号分隔 (wechat,zhihu,juejin)")
@click.option("--all", "-a", "all_platforms", is_flag=True, help="发布所有已配置的平台")
@click.option("--draft", is_flag=True, help="草稿模式，只保存草稿不正式发布")
@click.option("--dry-run", is_flag=True, help="试运行模式，只打印预览不实际调用API")
@click.option("--file", "-f", type=click.Path(exists=True), help="Markdown原文文件路径")
@click.option("--dist-dir", default="./dist", help="dist目录路径，默认 ./dist")
@click.option("--report", default=None, help="dry-run 报告输出文件路径 (如 publish-report.md)")
@click.version_option(version="0.1.0", prog_name="publish")
@click.pass_context
def app(
    ctx: click.Context,
    env: Optional[str],
    platforms: Optional[str],
    all_platforms: bool,
    draft: bool,
    dry_run: bool,
    file: Optional[str],
    dist_dir: str,
    report: Optional[str],
) -> None:
    if ctx.invoked_subcommand is not None:
        if env:
            load_dotenv(env)
        else:
            load_dotenv()
        return

    if not any([platforms, all_platforms, file, draft, dry_run, report]):
        click.echo(ctx.get_help())


@app.command("convert")
@click.argument("file", type=click.Path(exists=True))
@click.option("--target", "-t", multiple=True, help="目标平台 (wechat/zhihu/juejin)，可多次指定")
@click.option("--all", "-a", is_flag=True, help="转换所有支持的平台")
@click.option("--output", "-o", default="./dist", help="输出目录，默认 ./dist")
def convert_cmd(file: str, target: tuple, all: bool, output: str) -> None:
    md_text = _read_markdown(file)
    file_stem = Path(file).stem

    if all:
        platforms = get_supported_platforms()
    elif target:
        platforms = list(target)
    else:
        console.print("[yellow]请指定目标平台 --target 或使用 --all[/yellow]")
        sys.exit(1)

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    for platform in platforms:
        try:
            metadata = ArticleMetadata()
            converter_kwargs = {}

            if platform == "wechat":
                converter_kwargs["fail_on_image_error"] = False

            converter = get_converter(platform, **converter_kwargs)
            content, meta = converter.convert(md_text, metadata)

            if platform == "wechat":
                ext = ".html"
            else:
                ext = ".md"

            output_file = output_dir / f"{file_stem}.{platform}{ext}"
            output_file.write_text(content, encoding="utf-8")

            console.print(f"[green]✓[/green] [{platform}] 转换完成 -> {output_file}")
            if meta.title:
                console.print(f"    标题: {meta.title}")
            if meta.tags:
                console.print(f"    标签: {', '.join(meta.tags)}")
            if meta.summary:
                summary_preview = meta.summary[:50] + "..." if len(meta.summary) > 50 else meta.summary
                console.print(f"    摘要: {summary_preview}")

            meta_file = output_dir / f"{file_stem}.{platform}.json"
            meta_file.write_text(
                json.dumps(meta.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if platform == "wechat" and getattr(converter, 'failed_images', None):
                console.print(f"    [yellow]⚠[/yellow] 有 {len(converter.failed_images)} 张图片未上传到微信素材库")
                console.print(f"      发布时会自动重试上传，或使用 --dry-run 跳过")

        except Exception as e:
            console.print(f"[red]✗[/red] [{platform}] 转换失败: {e}")

    console.print(f"\n[bold green]全部转换完成，输出目录: {output_dir}[/bold green]")


@app.command("publish")
@add_publish_options
def publish_cmd(
    platforms: Optional[str],
    all_platforms: bool,
    draft: bool,
    dry_run: bool,
    file: Optional[str],
    dist_dir: str,
    report: Optional[str],
) -> None:
    _do_publish(platforms, all_platforms, draft, dry_run, file, dist_dir, report)


@app.command("doctor")
@click.option("--env", type=click.Path(exists=True), help="环境变量文件路径")
@click.option("--dist-dir", default="./dist", help="dist目录路径，默认 ./dist")
@click.option("--file", "-f", type=click.Path(exists=True), help="Markdown原文文件路径 (用于元信息检查)")
def doctor_cmd(env: Optional[str], dist_dir: str, file: Optional[str]) -> None:
    _do_doctor(env, dist_dir, file)


@app.group("platforms")
def platforms_cmd() -> None:
    pass


@platforms_cmd.command("list")
def platforms_list() -> None:
    configs = list_platforms()

    env_platforms = []
    for p in get_supported_platforms():
        if get_platform_config_from_env(p):
            env_platforms.append(p)

    if not configs and not env_platforms:
        console.print("[yellow]暂无已配置的平台[/yellow]")
        return

    table = Table(title="已配置的平台")
    table.add_column("名称", style="cyan")
    table.add_column("类型", style="green")
    table.add_column("来源", style="yellow")
    table.add_column("状态", style="magenta")

    for name, cfg in configs.items():
        has_key = "✓" if cfg.api_key or cfg.access_token else "✗"
        table.add_row(name, cfg.type, "配置文件", has_key)

    for p in env_platforms:
        if p not in configs:
            has_key = "✓"
            table.add_row(p, p, "环境变量", has_key)

    console.print(table)


@platforms_cmd.command("add")
@click.option("--name", prompt="平台名称", help="平台配置名称")
@click.option("--type", "ptype", type=click.Choice(["wechat", "zhihu", "juejin"]), prompt="平台类型")
def platforms_add(name: str, ptype: str) -> None:
    existing = get_platform(name)
    if existing:
        if not click.confirm(f"平台 '{name}' 已存在，是否覆盖？"):
            return

    api_key = click.prompt("API Key / App ID", default="")
    api_secret = ""
    access_token = ""

    if ptype == "wechat":
        api_secret = click.prompt("App Secret", default="")
    elif ptype in ("zhihu", "juejin"):
        access_token = click.prompt("Access Token", default="")

    params = {}
    if ptype == "juejin":
        category = click.prompt("分类 (前端/后端/Android 等)", default="前端")
        params["category"] = category
        topics_str = click.prompt("默认话题 (逗号分隔)", default="")
        if topics_str:
            params["topics"] = [t.strip() for t in topics_str.split(",")]

    config = PlatformConfig(
        name=name,
        type=ptype,
        api_key=api_key or None,
        api_secret=api_secret or None,
        access_token=access_token or None,
        params=params,
    )

    add_platform(config)
    console.print(f"[green]✓ 平台 '{name}' ({ptype}) 添加成功[/green]")


@platforms_cmd.command("remove")
@click.argument("name")
def platforms_remove(name: str) -> None:
    if remove_platform(name):
        console.print(f"[green]✓ 平台 '{name}' 已删除[/green]")
    else:
        console.print(f"[red]✗ 平台 '{name}' 不存在[/red]")


@platforms_cmd.command("show")
@click.argument("name")
def platforms_show(name: str) -> None:
    configs = load_config()
    cfg = _resolve_platform_config(name, configs)

    if not cfg:
        console.print(f"[red]✗ 平台 '{name}' 不存在或未配置[/red]")
        console.print(f"可设置 {name.upper()}_ACCESS_TOKEN 环境变量，或使用 'publish platforms add' 添加")
        return

    source = "配置文件" if name in configs else "环境变量"

    table = Table(title=f"平台配置: {name} ({source})")
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")

    table.add_row("类型", cfg.type)
    table.add_row("来源", source)
    table.add_row("API Key", cfg.api_key or "-")
    if cfg.api_secret:
        table.add_row("API Secret", "***")
    if cfg.access_token:
        table.add_row("Access Token", "***")
    if cfg.params:
        for k, v in cfg.params.items():
            if isinstance(v, list):
                v = ", ".join(v)
            table.add_row(k, str(v))

    console.print(table)


if __name__ == "__main__":
    app()
