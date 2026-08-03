"""Точка входа: python -m reportsdb <команда>."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import DB_PATH, RAW_DIR, load_mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reportsdb", description="Локальная БД отчётности SSRS"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", help="показать структуру исходного файла")
    p_profile.add_argument("file", type=Path)
    p_profile.add_argument("--sheet", default=None)
    p_profile.add_argument("--header-row", type=int, default=0)
    p_profile.add_argument("--config", type=Path, default=None)

    p_build = sub.add_parser("build", help="собрать БД из исходного файла")
    p_build.add_argument("file", type=Path, nargs="?", help="по умолчанию — из mapping.yml")
    p_build.add_argument("--db", type=Path, default=DB_PATH)
    p_build.add_argument(
        "--config", type=Path, default=None,
        help="конфиг маппинга; по умолчанию config/mapping.yml",
    )

    p_export = sub.add_parser("export-html", help="собрать самодостаточный HTML")
    p_export.add_argument("--db", type=Path, default=DB_PATH)
    p_export.add_argument("--out", type=Path, default=None)

    p_diag = sub.add_parser(
        "diagnose", help="сверить файл размеров с тем, что попало в базу"
    )
    p_diag.add_argument(
        "file", type=Path, nargs="?",
        help="файл размеров; по умолчанию — из mapping.yml",
    )
    p_diag.add_argument("--db", type=Path, default=DB_PATH)
    p_diag.add_argument("--config", type=Path, default=None)
    p_diag.add_argument(
        "--show-names", action="store_true",
        help="печатать имена таблиц (по умолчанию только числа)",
    )

    sub.add_parser("sample", help="сгенерировать синтетические данные в data/raw/")

    p_clear = sub.add_parser(
        "clear", help="стереть все данные, оставив пустую базу"
    )
    p_clear.add_argument("--db", type=Path, default=DB_PATH)
    p_clear.add_argument(
        "--keep-backup", action="store_true",
        help="не удалять reports.duckdb.bak (по умолчанию удаляется вместе с базой)",
    )
    p_clear.add_argument(
        "--yes", action="store_true", help="не спрашивать подтверждения",
    )

    args = parser.parse_args(argv)

    if args.command == "profile":
        from .profile_source import profile

        profile(args.file, args.sheet, args.header_row, args.config)
        return 0

    if args.command == "sample":
        from .sample_data import generate

        paths = generate()
        print("Созданы синтетические данные:")
        for label, path in paths.items():
            print(f"  {label:<8} {path}")
        print(
            "\nЧтобы собрать БД на них:\n"
            "  python -m reportsdb build data/raw/sample_reports.xlsx"
        )
        return 0

    if args.command == "build":
        from .etl import build, print_summary

        mapping = load_mapping(args.config)
        source = args.file
        if source is None:
            if not mapping.reports.file:
                raise SystemExit(
                    "Укажите файл: python -m reportsdb build data/raw/<файл>.xlsx\n"
                    "или пропишите reports.file в config/mapping.yml"
                )
            source = Path(mapping.reports.file)
            if not source.is_absolute():
                source = RAW_DIR / source
        if not source.exists():
            raise SystemExit(f"Файл не найден: {source}")

        stats = build(source, args.db, mapping)
        print_summary(stats)
        print(f"\nБД: {args.db}")
        return 0

    if args.command == "diagnose":
        from .diagnose import diagnose

        return diagnose(
            load_mapping(args.config), args.db, args.file, args.show_names
        )

    if args.command == "clear":
        from .etl import clear

        if not args.yes:
            # Действие необратимое, а команду легко набрать по ошибке вместо
            # `build`. Ответ читается с клавиатуры, ключ `--yes` — для скриптов.
            answer = input(
                f"Стереть все данные в {args.db}? Введите «да» для подтверждения: "
            )
            if answer.strip().lower() not in {"да", "yes", "y"}:
                print("Отменено, ничего не изменилось.")
                return 1

        result = clear(args.db, keep_backup=args.keep_backup)
        print(f"База очищена: {args.db}")
        print(f"Освобождено: {result.freed_bytes / 1024 / 1024:.1f} МБ")
        if result.backup_removed:
            print("Резервная копия reports.duckdb.bak удалена.")
        if result.raw_files_left:
            print(
                f"Исходные файлы в data/raw/ не тронуты: {result.raw_files_left}. "
                "Их нужно удалить отдельно, если они больше не нужны."
            )
        return 0

    if args.command == "export-html":
        from .export_html import export

        out = export(args.db, args.out)
        print(f"HTML собран: {out}")
        print(f"Размер: {out.stat().st_size / 1024:.0f} КБ")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
