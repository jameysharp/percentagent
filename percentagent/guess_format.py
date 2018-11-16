#!/usr/bin/env python

from collections import Counter
import copy
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
    _numeric_formats = "CymdHMS"

    def __init__(self, locale_set=None):
        if locale_set is None:
            locale_set = TimeLocaleSet.default()
        self.locale_set = locale_set
        strings = set(itertools.chain(locale_set.prefixes, locale_set.keywords, locale_set.suffixes))
        self.compiled = re.compile(r'(\d{1,2}|[+-]\d{4}|' + '|'.join(map(re.escape, sorted(strings, reverse=True))) + ')', re.I)

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

        Separators are not required between conversions, but they can help with
        ambiguity:

        >>> sorted(fmt for fmt, locales in parser.parse("210456"))
        ['%H%M%S', '%d%m%y']
        >>> parser.parse("21-04-56")
        [('%d-%m-%y', None)]
        >>> parser.parse("21:04:56")
        [('%H:%M:%S', None)]

        Using locale-specific strings can help avoid ambiguity too:

        >>> parser = DateParser(TimeLocaleSet(
        ...     mon={"Jan;Feb;Mar;Apr;May;Jun;Jul;Aug;Sep;Oct;Nov;Dec": ["en_US"]},
        ... ))
        >>> parser.parse("2018May05")
        [('%Y%b%d', frozenset({'en_US'}))]

        :param str s: text which contains a date and/or time
        :return: possible format strings, and corresponding locales
        :rtype: list(tuple(str, set(str) or None))
        """

        segments = self.compiled.split(self._whitespace.sub(" ", s))
        literals = segments[::2]
        raw = segments[1::2]

        if not raw:
            return []

        case = list(map(str.casefold, raw))
        prefixes = [{}] + [dict(self.locale_set.prefixes.get(match, ())) for match in case[:-1]]
        suffixes = [dict(self.locale_set.suffixes.get(match, ())) for match in case[1:]] + [{}]
        keywords = [self._lookup_keyword(match) + ((match, None),) for match in raw]
        groups = []
        for keyword, prefix, suffix in zip(keywords, prefixes, suffixes):
            if "y" in prefix:
                prefix["C"] = tuple(set(prefix["y"] + prefix.get("C", ())))
            groups.append([
                (
                    fmt,
                    locales,
                    prefix.get(fmt[-1]) if fmt[0] == "%" else None,
                    suffix.get(fmt[-1]) if fmt[0] == "%" else None,
                )
                for fmt, locales in keyword
            ])

        best_quality = None
        best_candidates = []

        # TODO: depth-first branch-and-bound and dynamic variable/value order
        partials = [_State().children(groups[0])]
        while partials:
            try:
                state = next(partials[-1])
            except StopIteration:
                partials.pop()
                continue

            if len(partials) < len(groups):
                partials.append(state.children(groups[len(partials)]))
                continue

            try:
                fmts, locales, quality = state.finish()
            except ValueError:
                continue

            if best_quality is not None and quality < best_quality:
                # We've seen better, so skip this one.
                continue

            if quality != best_quality:
                best_quality = quality
                best_candidates = []

            pattern = ''.join(lit + fmt for lit, fmt in zip(literals, fmts + ('',))).replace("%C%y", "%Y")
            best_candidates.append((pattern, locales))
        return best_candidates

    def _lookup_keyword(self, raw):
        keyword = raw.casefold()
        found = self.locale_set.keywords.get(keyword)
        if found:
            ret = { "%" + fmt: frozenset(locales) for fmt, locales in found }
            if "%O" in ret:
                locales = ret.pop("%O")
                # TODO: find the index of `keyword` in alt_digits
                ret.update(("%O" + fmt, locales) for fmt in self._numeric_formats)
            return tuple(ret.items())
        if keyword[0] in "+-":
            if keyword[1:].isdigit():
                return (("%z", None),)
        elif keyword.isdigit():
            value = int(keyword)
            legal = set("Cy")
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
            return tuple(("%" + fmt, None) for fmt in legal)
        return ()

class _State(object):
    def __init__(self):
        self.fmts = ()
        self.seen = frozenset()
        self.required_locales = None
        self.satisfied = Counter()
        self.globally_satisfied = 0
        self.was_conversion = True
        self.unless_conversion = None

    def children(self, options):
        for fmt, locales, prefix, suffix in options:
            new = copy.copy(self)

            if locales:
                if self.required_locales:
                    if locales.isdisjoint(self.required_locales):
                        continue
                    locales = locales.intersection(self.required_locales)
                new.required_locales = locales

            is_conversion = fmt[0] == "%"
            new.globally_satisfied += is_conversion

            if is_conversion:
                category = self._same_fields.get(fmt[-1], fmt[-1])
                if category in self.seen:
                    continue
                new.seen = self.seen.union((category,))

            local_satisfied = (
                None if is_conversion else self.unless_conversion,
                None if self.was_conversion else prefix,
            )
            for hint in local_satisfied:
                if hint:
                    new.satisfied = self.satisfied.copy()
                    new.satisfied.update(hint)
                elif hint is not None:
                    new.globally_satisfied += 1

            new.was_conversion = is_conversion
            new.unless_conversion = suffix

            new.fmts = self.fmts + (fmt,)
            yield new

    def finish(self):
        if self.seen.intersection(self._all_date_formats) and not self.seen.issuperset(self._min_date_formats):
            raise ValueError("incomplete date specification")

        if self.seen.intersection(self._all_time_formats) and not self.seen.issuperset(self._min_time_formats):
            raise ValueError("incomplete time specification")

        conversions = ''.join(
                self._same_fields.get(fmt[-1], fmt[-1])
                for fmt in self.fmts
                if fmt[0] == '%'
            )

        if self._bad_order.search(conversions):
            raise ValueError("prohibited conversion ordering")

        satisfied_locales = self.required_locales
        locally_satisfied = 0
        if self.required_locales:
            satisfied = [ (k, v) for k, v in self.satisfied.items() if k in self.required_locales ]
        else:
            satisfied = list(self.satisfied.items())
        if satisfied:
            locally_satisfied = max(v for k, v in satisfied)
            satisfied_locales = frozenset(
                locale for locale, count in satisfied
                if count == locally_satisfied
            )
        return self.fmts, satisfied_locales, self.globally_satisfied + locally_satisfied

    _same_fields = {
        'b': 'm',
    }
    _min_date_formats = "ymd"
    _all_date_formats = _min_date_formats + "Ca"
    _min_time_formats = "HM"
    _all_time_formats = _min_time_formats + "SpzZ"
    _bad_order = re.compile(r'C(?!y)|(?<!d)m(?!d)|(?<!H)M|(?<!M)S')

if __name__ == "__main__":
    parser = DateParser()
    examples = (
        "5/5/2018, 4:45:18 AM",
        "20180505T114518Z",
        "T nov   13 12:27:03 PST 2018",
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
