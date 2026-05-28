from native_drama_uploader.qt_bootstrap import configure_qt_runtime

configure_qt_runtime()

from native_drama_uploader.gui import main


if __name__ == "__main__":
    main()
