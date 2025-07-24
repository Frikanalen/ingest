#!/usr/bin/env python3
import logging

from app.manual_run import ArgumentParser

logging.basicConfig(level=logging.DEBUG)


def main():
    parser = ArgumentParser()
    parser.parse_and_execute()


if __name__ == "__main__":
    main()
