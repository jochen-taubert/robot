#!/usr/bin/env python3
"""Project entrypoint for the voice-controlled robot."""

from app.app import RobotApp


def main():
    RobotApp().run()


if __name__ == "__main__":
    main()
