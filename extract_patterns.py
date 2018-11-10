#!/usr/bin/env python

from collections import defaultdict
import pytz
import re

equivalents = {
    'e': 'd',
    'I': 'H',
    'k': 'H',
    'l': 'H',
    'y': 'Y',
}

text_keywords = {
    "abday": "a",
    "day": "A",
    "abmon": "b",
    "mon": "B",
    "am_pm": "p",
}

# Some patterns are common across so many locales that they are useless for
# guessing which locale the input came from, and should just be allowed for all
# locales.
date_patterns = [ order.format(fmt) for order in ("{}#", "#{}") for fmt in "Ymd" ]
global_patterns = {
    ':': ("H#", "M#", "#M", "#S"),
    '/': date_patterns,
    '-': date_patterns,
    'utc': ("#z",), # "UTC+hhmm"
}

merge_patterns = (
    ("p", ("am", "a.m.")),
    ("p", ("pm", "p.m.")),
)

# These symbols never provide semantic information about neighboring conversion
# specifiers.
ignore = ('['
        # whitespace and right-to-left markers
        "\\s\u202b\u202c"
        # parens and dot
        "()."
        # various kinds of https://en.wikipedia.org/wiki/Comma
        ",\xb7\u055d\u060c\u07f8\u1363\u1802\u1808\u2e41\u2e4c\u3001\ua4fe\ua60d\ua6f5\uff0c"
        ']*')
fmt_token = re.compile(ignore + r'%[-_0^#]?\d*[EO]?([a-zA-Z+%])' + ignore)

def extract(by_keyword):
    patterns = defaultdict(lambda: defaultdict(set))

    for v, locales in by_keyword["alt_digits"].items():
        for num in v.split(";"):
            patterns[num]["O"].update(locales)

    # TODO: extract patterns from by_keyword["era"]

    for v, locales in by_keyword["formats"].items():
        tokens = fmt_token.split(v)
        pairs = zip(tokens, tokens[1:])
        while True:
            try:
                prefix, fmt = next(pairs)
                fmt2, suffix = next(pairs)
            except StopIteration:
                break
            assert fmt == fmt2

            # We don't need to look at surrounding context to recognize the
            # names of weekdays, months, or morning/afternoon.
            if fmt.lower() in "abp":
                continue

            fmt = equivalents.get(fmt, fmt)
            if prefix != '':
                patterns[prefix.casefold()]["#" + fmt].update(locales)
            if suffix != '':
                patterns[suffix.casefold()][fmt + "#"].update(locales)

    for k, fmt in text_keywords.items():
        fmt = fmt.lower()
        for v, locales in by_keyword[k].items():
            for word in v.split(";"):
                patterns[word.strip().casefold()][fmt].update(locales)

    for pattern, fmts in global_patterns.items():
        patterns[pattern] = dict.fromkeys(fmts, frozenset())

    for fmt, merges in merge_patterns:
        merged = set.union(*(patterns[pattern][fmt] for pattern in merges))
        for pattern in merges:
            patterns[pattern][fmt] = merged

    for timezone in pytz.all_timezones:
        tz = pytz.timezone(timezone)
        if hasattr(tz, "_transition_info"):
            shortnames = set(tzname for _, _, tzname in tz._transition_info)
        else:
            shortnames = [tz._tzname]
        for tzname in shortnames:
            if tzname[0] not in "+-":
                patterns[tzname.casefold()]["Z"] = set()

    return patterns

if __name__ == "__main__":
    import json, sys
    by_keyword = json.load(sys.stdin)
    patterns = extract(by_keyword)

    for pattern, fmts in sorted(patterns.items()):
        #if len(fmts) <= 1:
        #    continue
        print("{!r}:".format(pattern))
        for fmt, locales in sorted(fmts.items()):
            print("- {}: {}".format(fmt, ' '.join(sorted(locales))))
        print()
