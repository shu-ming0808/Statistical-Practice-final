"""Rebuild PTT-only reports with the shared Chinese word-cloud renderer."""
import sys
from threads_web_report import main as report_main


def main(argv=None):
    # The final argument locks the platform even if an earlier flag disagrees.
    return report_main([*(sys.argv[1:] if argv is None else argv), "--source", "ptt_public_web"])


if __name__ == "__main__":
    raise SystemExit(main())
