#!/usr/bin/env python

from collections import defaultdict
import itertools
import re

import extract_patterns

class DateParser(object):
    whitespace = re.compile(r'\s+')
    numeric_formats = "CYmdHMS"
    same_fields = {
        'b': 'm',
    }

    def __init__(self, patterns):
        self.patterns = patterns
        self.compiled = re.compile(r'(\d+|[+-]\d{4}|' + '|'.join(map(re.escape, sorted(patterns, reverse=True))) + ')', re.I)

    @classmethod
    def _numeric_group(cls, value):
        fmt = "0"
        if value[0] in "+-":
            fmt = "z"
            value = value[1:]
        assert value.isdigit(), value
        return { fmt: set() }

    def parse(self, s):
        segments = self.compiled.split(self.whitespace.sub(" ", s))
        best_quality = None
        best_candidates = []
        for quality, pattern, locales in self._candidates(segments):
            if best_quality is not None and quality > best_quality:
                # We've seen better, so skip this one.
                continue
            if quality != best_quality:
                best_quality = quality
                best_candidates = []
            best_candidates.append((pattern, locales))
        return best_candidates

    def _candidates(self, segments):
        literals = segments[::2]
        raw = segments[1::2]
        for candidate in itertools.product(*self._groups(raw)):
            fmts, locales = zip(*candidate)
            locales = self._intersect_locales(locales)
            if locales == set():
                continue
            if not self._validate_conversions(fmts):
                continue
            quality = sum(len(fmt) for fmt in fmts if fmt[0] != "%")
            pattern = ''.join(lit + fmt for lit, fmt in zip(literals, fmts + ('',)))
            yield quality, pattern, locales

    def _groups(self, raw):
        groups = [ self.patterns.get(match.casefold()) or self._numeric_group(match) for match in raw ]
        prefixes = [{}] + [ { k[1:]: v for k, v in g.items() if k[0] == '#' } for g in groups[:-1] ]
        keywords = [ { k: v for k, v in g.items() if '#' not in k } for g in groups ]
        suffixes = [ { k[:-1]: v for k, v in g.items() if k[-1] == '#' } for g in groups[1:] ] + [{}]
        return [ self._unify(*stuff) for stuff in zip(raw, prefixes, keywords, suffixes) ]

    @classmethod
    def _unify(cls, raw, *group):
        ret = defaultdict(list)
        for g in group:
            for fmt, locales in g.items():
                ret[fmt].append(set(locales))

        prefix = "%"
        if "O" in ret:
            prefix = "%O"

        retain = set(ret.keys())
        # We can match numeric formats if and only if this field turned out to
        # be numeric.
        if retain.intersection("O0"):
            legal = set()
            if "0" in ret:
                value = int(raw)
                legal.update("Y")
                if value < 100:
                    legal.update("C")
                    if value <= 60:
                        legal.update("S")
                        if value <= 59:
                            legal.update("M")
                            if value <= 23:
                                legal.update("H")
                            if 1 <= value <= 31:
                                legal.update("d")
                                if value <= 12:
                                    legal.update("m")
            else:
                # TODO: find the locale and index of `raw` in alt_digits
                legal = cls.numeric_formats

            retain.intersection_update(legal)
            if not retain:
                # If we don't have any guesses, guess everything.
                retain = legal
        else:
            retain.difference_update(cls.numeric_formats)

        ret = { prefix + k: cls._intersect_locales(ret.get(k, ())) for k in retain }
        ret = sorted(ret.items(), key=lambda item: len(item[1] or ()), reverse=True)
        ret.append((raw, None))
        return ret

    @staticmethod
    def _intersect_locales(locales):
        locales = list(filter(None, locales))
        if not locales:
            return None
        return set.intersection(*locales)

    min_date_formats = "Ymd"
    all_date_formats = min_date_formats + "a"
    min_time_formats = "HM"
    all_time_formats = min_time_formats + "SpzZ"
    bad_order = re.compile(r'(?<!d)m(?!d)|(?<!H)M|(?<!M)S')

    @classmethod
    def _validate_conversions(cls, fmts):
        conversions = ''.join(
                cls.same_fields.get(fmt[-1], fmt[-1])
                for fmt in fmts
                if fmt[0] == '%'
            )

        fmt_set = set(conversions)

        # No duplicate conversions.
        if len(fmt_set) != len(conversions):
            return False

        if fmt_set.intersection(cls.all_date_formats) and not fmt_set.issuperset(cls.min_date_formats):
            return False

        if fmt_set.intersection(cls.all_time_formats) and not fmt_set.issuperset(cls.min_time_formats):
            return False

        if cls.bad_order.search(conversions):
            return False

        return True

if __name__ == "__main__":
    import json
    with open("lc_time-glibc.json") as f:
        locales = json.load(f)
    parser = DateParser(extract_patterns.extract(locales))
    examples = (
        "5/5/2018, 4:45:18 AM",
        "2018-05-05T11:45:18.0000000Z",
        "Fri Nov  9 17:49:24 PST 2018",
        "Fra Nov  9 17:57:39 PST 2018",
        "Lw5 Nov  9 17:57:39 PST 2018",
        "Jimaata, Sadaasa  9,  5:57:39 WB PST 2018",
        "Arbe, November  9,  5:57:39 hawwaro PST 2018",
        "Jim KIT  9  5:57:39 galabnimo PST 2018",
        "ዓርቢ፣ ኖቬምበር  9 መዓልቲ  5:57:39 ድሕር ሰዓት PST 2018 ዓ/ም",
        "2018年 11月  9日 金曜日 17:23:30 PST",
        "公曆 20十八年 十一月 九日 週五 十七時57分39秒",
        "2018. 11. 09. (금) 17:23:23 PST",
        "п'ятниця, 9 листопада 2018 17:57:39 -0800",
        "Misálá mítáno 9 sánzá ya zómi na mɔ̌kɔ́ 2018, 17:57:39 (UTC-0800)",
        "جۆمعه ۰۹ نوْوامبر ۱۸، ساعات ۱۷:۵۷:۳۹ (PST)",
    )
    for example in examples:
        print(repr(example))
        for fmt, locales in parser.parse(example):
            print("- {!r} ({})".format(fmt, ' '.join(sorted(locales or ["C"]))))
        print()
