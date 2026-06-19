import os
import sys
import json
from pathlib import Path
from typing import List, Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .converters import get_converter, get_supported_platforms
from .publishers import get_publisher
from .config import load_config, add_platform, remove_platform, list_platforms, get_platform
from .models import ArticleMetadata, PlatformConfig

console = Console()


def _parse_metadata_file(md_path: Path) -> ArticleMetadata:
    metadata = ArticleMetadata()
    return metadata


def _ensure_dist_dir() -> Path:
    dist_dir = Path.cwd() / "dist"
    dist_dir.mkdir(exist_ok=True)
    return dist_dir


def _read_markdown(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]错误: 文件不存在: {file_path}[/red]")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


@click.group()
@click.option("--env", type=click.Path(exists=True), help="环境变量文件路径")
@click.version_option(version="0.1.0", prog_name="publish")
def app(env: Optional[str]) -> None:
    if env:
        load_dotenv(env)
    else:
        load_dotenv()


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
            converter = get_converter(platform)
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

        except Exception as e:
            console.print(f"[red]✗[/red] [{platform}] 转换失败: {e}")

    console.print(f"\n[bold green]全部转换完成，输出目录: {output_dir}[/bold green]")


@app.command("publish")
@click.option("--platforms", "-p", default=None, help="发布平台，逗号分隔 (wechat,zhihu,juejin)")
@click.option("--all", "-a", is_flag=True, help="发布所有已配置的平台")
@click.option("--draft", is_flag=True, help="草稿模式，只保存草稿不正式发布")
@click.option("--dry-run", is_flag=True, help="试运行模式，只打印不实际调用API")
@click.option("--file", "-f", type=click.Path(exists=True), help="Markdown原文文件路径")
@click.option("--dist-dir", default="./dist", help="dist目录路径，默认 ./dist")
def publish_cmd(
    platforms: Optional[str],
    all: bool,
    draft: bool,
    dry_run: bool,
    file: Optional[str],
    dist_dir: str,
) -> None:
    configs = load_config()

    if all:
        target_platforms = list(configs.keys())
    elif platforms:
        target_platforms = [p.strip() for p in platforms.split(",")]
    else:
        console.print("[yellow]请指定发布平台 --platforms 或使用 --all[/yellow]")
        sys.exit(1)

    if not target_platforms:
        console.print("[red]没有可用的平台配置，请先使用 'publish platforms add' 添加平台[/red]")
        sys.exit(1)

    if file:
        md_text = _read_markdown(file)
        file_stem = Path(file).stem
    else:
        dist_path = Path(dist_dir)
        md_files = list(dist_path.glob("*.md"))
        if not md_files:
            console.print(f"[red]未在 {dist_dir} 中找到 Markdown 文件，请使用 --file 指定[/red]")
            sys.exit(1)
        file_stem = md_files[0].stem
        md_text = md_files[0].read_text(encoding="utf-8")

    if dry_run:
        console.print(Panel("[yellow] DRY RUN 模式 - 不会实际调用API [/yellow]", expand=False))

    if draft:
        console.print(Panel("[blue] DRAFT 模式 - 仅保存草稿 [/blue]", expand=False))

    console.print(f"\n目标平台: {', '.join(target_platforms)}")
    console.print(f"文章: {file_stem}\n")

    results = []

    for platform in target_platforms:
        if platform not in configs:
            error_msg = f"平台 '{platform}' 未配置，请先使用 'publish platforms add' 添加"
            console.print(f"[red]✗[/red] [{platform}] {error_msg}")
            results.append({
                "success": False,
                "platform": platform,
                "error": error_msg,
            })
            continue

        try:
            metadata = ArticleMetadata()
            converter = get_converter(platform)
            content, meta = converter.convert(md_text, metadata)

            publisher = get_publisher(platform, configs[platform], dry_run=dry_run)
            result = publisher.publish(content, meta, draft=draft)

            if result.success:
                status = "草稿" if result.is_draft else "已发布"
                console.print(f"[green]✓[/green] [{platform}] {status}: {result.url}")
            else:
                console.print(f"[red]✗[/red] [{platform}] 失败: {result.error}")

            results.append(result.model_dump())

        except Exception as e:
            console.print(f"[red]✗[/red] [{platform}] 异常: {e}")
            results.append({
                "success": False,
                "platform": platform,
                "error": str(e),
            })

    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    console.print()
    console.print(f"[bold]发布结果:[/bold] 成功 [green]{success_count}[/green] 个，失败 [red]{fail_count}[/red] 个")

    if fail_count > 0 and success_count == 0:
        sys.exit(1)


@app.group("platforms")
def platforms_cmd() -> None:
    pass


@platforms_cmd.command("list")
def platforms_list() -> None:
    configs = list_platforms()

    if not configs:
        console.print("[yellow]暂无已配置的平台[/yellow]")
        return

    table = Table(title="已配置的平台")
    table.add_column("名称", style="cyan")
    table.add_column("类型", style="green")
    table.add_column("状态", style="yellow")

    for name, cfg in configs.items():
        has_key = "✓" if cfg.api_key or cfg.access_token else "✗"
        table.add_row(name, cfg.type, has_key)

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
    cfg = get_platform(name)
    if not cfg:
        console.print(f"[red]✗ 平台 '{name}' 不存在[/red]")
        return

    table = Table(title=f"平台配置: {name}")
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")

    table.add_row("类型", cfg.type)
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
