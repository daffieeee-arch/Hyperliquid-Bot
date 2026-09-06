"""Run the PAPER-only control service with the pinned uvicorn."""

import uvicorn

from hyperliquid_bot.control_service.app import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
