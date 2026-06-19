import os
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown

from .converters import get_converter, get_supported_platforms
from .publishers import get_publisher, get_platform_config_from_env
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


def _get_platform_config(platform: str, configs: Dict[str, PlatformConfig]) -> Optional[PlatformConfig]:
    if platform in configs:
        return configs[platform]
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


def _do_publish(
    platforms: Optional[str],
    all_platforms: bool,
    draft: bool,
    dry_run: bool,
    file: Optional[str],
    dist_dir: str,
) -> None:
    configs = load_config()

    if all_platforms:
        target_platforms = []
        for p in get_supported_platforms():
            if _get_platform_config(p, configs):
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

    for platform in target_platforms:
        config = _get_platform_config(platform, configs)
        if not config:
            error_msg = (
                f"平台 '{platform}' 未配置凭证。\n"
                f"请设置 {platform.upper()}_ACCESS_TOKEN (或 {platform.upper()}_API_KEY + {platform.upper()}_API_SECRET) \n"
                f"环境变量，或使用 'publish platforms add' 添加配置。"
            )
            console.print(f"[red]✗[/red] [{platform}] 配置缺失")
            console.print(f"    {error_msg}")
            results.append({
                "success": False,
                "platform": platform,
                "error": error_msg,
            })
            continue

        content = None
        metadata = ArticleMetadata()

        if md_text:
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
            except Exception as e:
                console.print(f"[red]✗[/red] [{platform}] 转换失败: {e}")
                results.append({
                    "success": False,
                    "platform": platform,
                    "error": str(e),
                })
                continue
        else:
            dist_result = _read_dist_file(Path(dist_dir), file_stem, platform)
            if dist_result is None:
                ext = ".html" if platform == "wechat" else ".md"
                expected_file = f"{file_stem}.{platform}{ext}"
                error_msg = f"dist 目录中缺少该平台文件: {expected_file}"
                console.print(f"[yellow]⚠[/yellow] [{platform}] {error_msg}，跳过")
                results.append({
                    "success": False,
                    "platform": platform,
                    "error": error_msg,
                })
                continue
            content, metadata = dist_result

        if dry_run:
            publisher = get_publisher(platform, config, dry_run=True)
            info = publisher.get_dry_run_info(content, metadata, draft)
            _print_dry_run_preview(info)
            result = publisher.publish(content, metadata, draft=draft)
        else:
            try:
                publisher = get_publisher(platform, config, dry_run=False)
                result = publisher.publish(content, metadata, draft=draft)
            except Exception as e:
                console.print(f"[red]✗[/red] [{platform}] 异常: {e}")
                results.append({
                    "success": False,
                    "platform": platform,
                    "error": str(e),
                })
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

    if fail_count > 0 and success_count == 0:
        sys.exit(1)


_publish_options = [
    click.option("--platforms", "-p", default=None, help="发布平台，逗号分隔 (wechat,zhihu,juejin)"),
    click.option("--all", "-a", "all_platforms", is_flag=True, help="发布所有已配置的平台"),
    click.option("--draft", is_flag=True, help="草稿模式，只保存草稿不正式发布"),
    click.option("--dry-run", is_flag=True, help="试运行模式，只打印预览不实际调用API"),
    click.option("--file", "-f", type=click.Path(exists=True), help="Markdown原文文件路径"),
    click.option("--dist-dir", default="./dist", help="dist目录路径，默认 ./dist"),
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

        if has_platforms or has_all or has_file or has_draft or has_dry_run:
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
) -> None:
    if ctx.invoked_subcommand is not None:
        if env:
            load_dotenv(env)
        else:
            load_dotenv()
        return

    if not any([platforms, all_platforms, file, draft, dry_run]):
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
) -> None:
    _do_publish(platforms, all_platforms, draft, dry_run, file, dist_dir)


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
    cfg = _get_platform_config(name, configs)

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
