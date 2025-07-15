#!/usr/bin/env python3
# encoding: utf-8
import logging

from lib.argparser import ArgumentParser

logging.basicConfig(level=logging.DEBUG)


def main():
    parser = ArgumentParser()
    parser.parse_and_execute()


if __name__ == "__main__":
    main()
