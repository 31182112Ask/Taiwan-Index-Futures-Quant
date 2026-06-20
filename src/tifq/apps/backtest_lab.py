"""Compatibility entry point for the supported Streamlit Backtest Lab."""

from tifq.interfaces.streamlit.app import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
