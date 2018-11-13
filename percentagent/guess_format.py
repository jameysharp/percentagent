#!/usr/bin/env python

from collections import defaultdict
import itertools
import re

from percentagent.extract_patterns import TimeLocaleSet

class DateParser(object):
    """
    Infer :manpage:`strftime(3)`-style format strings that could have produced
    given date and/or time strings.

    If you don't provide a locale set, then one will be constructed for you
    with :py:meth:`TimeLocaleSet.default`. This is usually what you want, but
    you can construct special-purpose sets if needed.

    This class precomputes some large data structures when constructed, so you
    should reuse the same instance for multiple parses, if possible.

    Instances of this class may safely be used from multiple threads.

    :param TimeLocaleSet locale_set: locales to consider when parsing timestamps
    """

    _whitespace = re.compile(r'\s+')
    _numeric_formats = "CYmdHMS"
    _same_fields = {
        'b': 'm',
    }

    def __init__(self, locale_set=None):
        if locale_set is None:
            locale_set = TimeLocaleSet.default()
        self.patterns = locale_set.extract_patterns()
        self.compiled = re.compile(r'(\d+|[+-]\d{4}|' + '|'.join(map(re.escape, sorted(self.patterns, reverse=True))) + ')', re.I)

    @classmethod
    def _numeric_group(cls, value):
        fmt = "0"
        if value[0] in "+-":
            fmt = "z"
            value = value[1:]
        assert value.isdigit(), value
        return ((fmt, ()),)

    def parse(self, s):
        """
        Infer format strings for a single timestamp.

        For example:

        >>> parser = DateParser(TimeLocaleSet())
        >>> parser.parse("2018-05-05")
        [('%Y-%m-%d', None), ('%Y-%d-%m', None)]

        That output indicates that the input can be explained by either
        year-month-day order or year-day-month order, and there are no hints
        indicating which locale the date was formatted for.

        On the other hand, "13" is too large to be a month number, so this
        example is unambiguous:

        >>> parser.parse("2018-05-13")
        [('%Y-%m-%d', None)]

        Using locale-specific strings can help avoid ambiguity too:

        >>> parser = DateParser(TimeLocaleSet(
        ...     mon={"Jan;Feb;Mar;Apr;May;Jun;Jul;Aug;Sep;Oct;Nov;Dec": ["en_US"]},
        ... ))
        >>> parser.parse("2018May05")
        [('%Y%b%d', {'en_US'})]

        :param str s: text which contains a date and/or time
        :return: possible format strings, and corresponding locales
        :rtype: list(tuple(str, set(str) or None))
        """

        segments = self.compiled.split(self._whitespace.sub(" ", s))
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
        # FIXME: evaluating the full cartesian product is inefficient
        # TODO: depth-first branch-and-bound and dynamic variable/value order
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
        prefixes = [{}] + [ { k[1:]: v for k, v in g if k[0] == '#' } for g in groups[:-1] ]
        keywords = [ { k: v for k, v in g if '#' not in k } for g in groups ]
        suffixes = [ { k[:-1]: v for k, v in g if k[-1] == '#' } for g in groups[1:] ] + [{}]
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
                legal = cls._numeric_formats

            retain.intersection_update(legal)
            if not retain:
                # If we don't have any guesses, guess everything.
                retain = legal
        else:
            retain.difference_update(cls._numeric_formats)

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

    _min_date_formats = "Ymd"
    _all_date_formats = _min_date_formats + "a"
    _min_time_formats = "HM"
    _all_time_formats = _min_time_formats + "SpzZ"
    _bad_order = re.compile(r'(?<!d)m(?!d)|(?<!H)M|(?<!M)S')

    @classmethod
    def _validate_conversions(cls, fmts):
        conversions = ''.join(
                cls._same_fields.get(fmt[-1], fmt[-1])
                for fmt in fmts
                if fmt[0] == '%'
            )

        fmt_set = set(conversions)

        # No duplicate conversions.
        if len(fmt_set) != len(conversions):
            return False

        if fmt_set.intersection(cls._all_date_formats) and not fmt_set.issuperset(cls._min_date_formats):
            return False

        if fmt_set.intersection(cls._all_time_formats) and not fmt_set.issuperset(cls._min_time_formats):
            return False

        if cls._bad_order.search(conversions):
            return False

        return True

if __name__ == "__main__":
    parser = DateParser()
    examples = (
        "5/5/2018, 4:45:18 AM",
        "2018-05-05T11:45:18.0000000Z",
        "Fri Nov  9 17:49:24 PST 2018",
        "Fra Nov  9 17:57:39 PST 2018",
        "Lw5 Nov  9 17:57:39 PST 2018",
        "Dydd Mercher 08 mis Awst 2018 08:08:08 AWST",
        "Jimaata, Sadaasa  9,  5:57:39 WB PST 2018",
        "Arbe, November  9,  5:57:39 hawwaro PST 2018",
        "Jim KIT  9  5:57:39 galabnimo PST 2018",
        "ዓርቢ፣ ኖቬምበር  9 መዓልቲ  5:57:39 ድሕር ሰዓት PST 2018 ዓ/ም",
        "2018年 11月  9日 金曜日 17:23:30 PST",
        "公曆 20十八年 十一月 九日 週五 十七時57分39秒",
        "2018. 11. 09. (금) 17:23:23 PST",
        "2018년 11월 09일 (금) 오후 09시 15분 10초",
        "п'ятниця, 9 листопада 2018 17:57:39 -0800",
        "Misálá mítáno 9 sánzá ya zómi na mɔ̌kɔ́ 2018, 17:57:39 (UTC-0800)",
        "جۆمعه ۰۹ نوْوامبر ۱۸، ساعات ۱۷:۵۷:۳۹ (PST)",
    )
    for example in examples:
        print(repr(example))
        for fmt, locales in parser.parse(example):
            print("- {!r} ({})".format(fmt, ' '.join(sorted(locales or ["C"]))))
        print()
