import sys

from novel_narrator.cli.commands import run
from novel_narrator.cli.menus import main_menu


def main():
    try:
        if len(sys.argv) > 1:
            sys.exit(run(sys.argv[1:]))
        main_menu()
    except KeyboardInterrupt:
        print("\n\nInterrompido")


if __name__ == "__main__":
    main()
