from novel_narrator.cli.menus import main_menu


def main():
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nInterrompido")


if __name__ == "__main__":
    main()
