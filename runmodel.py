"""Fleet Sizing Optimizer — Interactive CLI entry point.

Usage:
    python runmodel.py
"""
from pathlib import Path

from fleet_sizing.data import load_data
from fleet_sizing.i18n import set_language, load_saved_language, t
from fleet_sizing.cli import (
    console,
    show_welcome,
    show_error,
)

DATA_DIR = Path(__file__).parent / "data"


def main() -> None:
    set_language(load_saved_language())
    try:
        show_welcome()

        try:
            pre = load_data(DATA_DIR)
        except FileNotFoundError as exc:
            show_error(t("main.data_not_found", exc=exc))
            console.print(t("main.excel_hint"))
            return
        except Exception as exc:
            show_error(t("main.load_error", exc=exc))
            return

        from fleet_sizing.nl_interface import run_interactive_whatif
        try:
            run_interactive_whatif(pre=pre)
        except KeyboardInterrupt:
            console.print(t("main.interrupted"))
        except Exception as exc:
            show_error(t("main.whatif_error", exc=exc))
            import traceback
            console.print("\n[dim]Full traceback:[/dim]")
            traceback.print_exc()

    except KeyboardInterrupt:
        console.print(t("main.global_interrupted"))
    except Exception as exc:
        show_error(t("main.unexpected_error", exc=exc))
        import traceback
        console.print("\n[dim]Full traceback:[/dim]")
        traceback.print_exc()


if __name__ == "__main__":
    main()
