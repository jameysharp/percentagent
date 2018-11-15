#!/usr/bin/env python

from collections import defaultdict
import json
from pkg_resources import resource_stream
import pytz
import re

class _InternTable(dict):
    """
    A callable which returns a value equal to its argument, but if it's called
    twice with equivalent values, it always returns the object that was passed
    to it first. As long as the objects are immutable, this saves memory
    without changing the behavior of the program.
    """

    def __call__(self, v):
        return self.setdefault(v, v)

class TimeLocaleSet(object):
    """
    Structured information about how a set of locales express dates and times.
    """

    @classmethod
    def from_json(cls, f):
        """
        Load a locale set from a JSON-formatted stream, such as one produced by
        ``utils/lc_time``.

        :return: the loaded locale set
        """

        return cls(**json.load(f))

    @classmethod
    def default(cls, provider="glibc"):
        """
        Load a locale set that was distributed with this package. See
        ``percentagent/locales/`` for the available sets.

        :return: the loaded locale set
        """

        path = "locales/{}.json".format(provider)
        with resource_stream(__name__, path) as f:
            return cls.from_json(f)

    @classmethod
    def _localized_conversion(cls, uniq, keywords, fmt, d):
        for v, locales in d.items():
            for word in v.split(";"):
                keywords[word.strip().casefold()][fmt].update(map(uniq, locales))

    _equivalents = {
        'e': 'd',
        'I': 'H',
        'k': 'H',
        'l': 'H',
        'Y': 'y',
    }

    # Some patterns are common across so many locales that they are useless for
    # guessing which locale the input came from, and should just be allowed for all
    # locales.
    _global_prefixes = (
        (":", "MS"),
        ("/", "Cymd"),
        ("-", "Cymd"),
        ("utc", "z"), # "UTC+hhmm"
        ("t", "H"), # ISO 8601: ...%dT%H...
    )
    _global_suffixes = (
        (":", "HM"),
        ("/", "ymd"),
        ("-", "ymd"),
        ("t", "d"), # ISO 8601: ...%dT%H...
    )

    _merge_patterns = (
        ("p", ("am", "a.m.")),
        ("p", ("pm", "p.m.")),
    )

    # These symbols never provide semantic information about neighboring conversion
    # specifiers.
    _ignore = ('['
            # whitespace and right-to-left markers
            "\\s\u202b\u202c"
            # parens and dot
            "()."
            # various kinds of https://en.wikipedia.org/wiki/Comma
            ",\xb7\u055d\u060c\u07f8\u1363\u1802\u1808\u2e41\u2e4c\u3001\ua4fe\ua60d\ua6f5\uff0c"
            ']*')

    _fmt_token = re.compile(_ignore + r'%[-_0^#]?\d*[EO]?([a-zA-Z+%])' + _ignore)
    """
    A compiled regular expression to match :manpage:`strftime(3)`-style
    conversion specifiers. This regex contains a single group which returns the
    final conversion specifier character, skipping any flags, field widths, or
    modifiers. The :py:meth:`~re.Pattern.findall` method will return a list of
    just the conversion specifier characters; the :py:meth:`~re.Pattern.split`
    method will return the same but alternating with non-conversion text.
    """

    def __init__(self, formats=None, day=None, mon=None, am_pm=None, alt_digits=None, era=None):
        """
        All parameters are dictionaries which map a string to a set of locales
        in which that string is used.

        Except for :py:obj:`formats`, the dictionary keys are
        semicolon-separated (``;``) ordered lists. Their semantics are
        documented in :manpage:`locale(5)`.

        :param formats: Sample :manpage:`strftime(3)` format strings to extract
            prefix and suffix patterns from.
        :param day: Names of days of the week.
        :param mon: Names of months.
        :param am_pm: Strings indicating times before or after noon.
        :param alt_digits: Numbers from writing systems which do not use
            Unicode digits.
        :param era: Definitions of how years are counted and displayed.
        """

        uniqlocales = _InternTable()
        uniqlocalesets = _InternTable()

        keywords = defaultdict(lambda: defaultdict(set))
        self._localized_conversion(uniqlocales, keywords, "a", day or {})
        self._localized_conversion(uniqlocales, keywords, "b", mon or {})
        self._localized_conversion(uniqlocales, keywords, "p", am_pm or {})
        self._localized_conversion(uniqlocales, keywords, "O", alt_digits or {})

        for fmt, merges in self._merge_patterns:
            merged = set.union(*(keywords[pattern][fmt] for pattern in merges))
            for pattern in merges:
                keywords[pattern][fmt] = merged

        for timezone in pytz.all_timezones:
            tz = pytz.timezone(timezone)
            if hasattr(tz, "_transition_info"):
                shortnames = set(tzname for _, _, tzname in tz._transition_info)
            else:
                shortnames = [tz._tzname]
            for tzname in shortnames:
                if tzname[0] not in "+-":
                    keywords[tzname.casefold()]["Z"] = frozenset()

        self._keywords = {
            pattern: tuple(
                (fmt, uniqlocalesets(tuple(sorted(locales))))
                for fmt, locales in fmts.items()
            )
            for pattern, fmts in keywords.items()
        }

        prefixes = defaultdict(lambda: defaultdict(set))
        suffixes = defaultdict(lambda: defaultdict(set))

        # TODO: extract patterns from era

        for v, locales in (formats or {}).items():
            tokens = iter(self._fmt_token.split(v))
            prefix = next(tokens)
            for fmt, suffix in zip(tokens, tokens):
                # We don't need to look at surrounding context to recognize the
                # names of weekdays, months, or morning/afternoon.
                if fmt.lower() not in "abp":
                    fmt = self._equivalents.get(fmt, fmt)
                    if prefix != '':
                        prefixes[prefix.casefold()][fmt].update(map(uniqlocales, locales))
                    if suffix != '':
                        suffixes[suffix.casefold()][fmt].update(map(uniqlocales, locales))

                # This conversion's suffix is the next conversion's prefix.
                prefix = suffix

        for pattern, fmts in self._global_prefixes:
            prefixes[pattern] = dict.fromkeys(fmts, frozenset())

        for pattern, fmts in self._global_suffixes:
            suffixes[pattern] = dict.fromkeys(fmts, frozenset())

        self._prefixes = {
            pattern: tuple(
                (fmt, uniqlocalesets(tuple(sorted(locales))))
                for fmt, locales in fmts.items()
            )
            for pattern, fmts in prefixes.items()
        }

        self._suffixes = {
            pattern: tuple(
                (fmt, uniqlocalesets(tuple(sorted(locales))))
                for fmt, locales in fmts.items()
            )
            for pattern, fmts in suffixes.items()
        }

    @property
    def keywords(self):
        """
        Group conversion specifiers by the non-numeric strings they can
        produce. This includes these specifiers:

        - Weekday names: ``%a``
        - Month names: ``%b``
        - AM/PM: ``%p``
        - Timezone abbreviations: ``%Z``
        - Non-decimal numbers: ``%O`` prefix (e.g. ``%Om`` for months)

        >>> glibc = TimeLocaleSet.default('glibc').keywords

        Many strings can only be produced by a single conversion specifier in a
        single locale. For example, according to the glibc locale database,
        "Agustus" is the ``id_ID`` (Indonesian) word for the 8th month, and
        does not appear in any other locale.

        >>> sorted(glibc['agustus'])
        [('b', ('id_ID',))]

        However, other strings can be ambiguous. For example, "Ahad" is the
        word for Sunday in ``ms_MY`` (the Malay language locale for Malaysia),
        but the word for Wednesday in ``kab_DZ`` (the Kabyle language locale
        for Algeria). These languages are from entirely different language
        families but we can't tell them apart if all we see is this one word.
        However, in either case we do know that the word refers to a weekday.

        >>> sorted(glibc['ahad'])
        [('a', ('kab_DZ', 'ms_MY'))]

        Sometimes, without context, we can't even tell which role a word plays.
        "An" is the word for Tuesday in ``lt_LT`` (Lithuanian), but hours
        before noon are distinguished with "AN" in ``ak_GH`` (the Akan locale
        for Ghana).

        >>> sorted(glibc['an'])
        [('a', ('lt_LT',)), ('p', ('ak_GH',))]

        Similarly, "AWST" is the timezone abbreviation for Australian Western
        Standard Time, while "Awst" is the ``cy_GB`` (Welsh) word for the 8th
        month.

        >>> sorted(glibc['awst'])
        [('Z', ()), ('b', ('cy_GB',))]

        Finally, in Chinese, Monday through Saturday are abbreviated using the
        numbers 1-6, and those numbers are written using the same characters in
        Japanese. So if we see those numbers, they could either be from numeric
        conversions such as ``%Od``, or from the abbreviated weekday
        conversion, ``%a``.

        >>> sorted(glibc['一'])
        [('O', ('ja_JP', 'lzh_TW')), ('a', ('cmn_TW', 'hak_TW', 'lzh_TW', 'nan_TW', 'yue_HK', 'zh_CN', 'zh_HK', 'zh_SG', 'zh_TW'))]
        """
        return self._keywords

    @property
    def prefixes(self):
        """
        Group conversion specifiers by the strings which may precede them.

        >>> glibc = TimeLocaleSet.default('glibc').prefixes

        In ``vi_VN`` (Vietnamese), "tháng" means "month", and "năm" means
        "year". Within the glibc locale database, we find that these words are
        used as prefix to the numeric value in question:

        >>> sorted(glibc['tháng'])
        [('m', ('vi_VN',))]
        >>> sorted(glibc['năm'])
        [('y', ('vi_VN',))]
        """
        return self._prefixes

    @property
    def suffixes(self):
        """
        Group conversion specifiers by the strings which may follow them.

        >>> suffixes = TimeLocaleSet(formats={
        ...     '%a, %Y.eko %bren %da': {'eu_ES'},
        ...     '%Y年%m月%d日': {'ja_JP'},
        ... }).suffixes

        In ``eu_ES`` (the Basque locale for Spain), year/month/day are followed
        by "eko", "ren", and "a", respectively. However, in our sample format
        string, "ren" follows ``%b``, which is the name of a month, not its
        number. So we don't extract it as a suffix; we rely on month names
        being sufficiently distinctive instead.

        >>> sorted(suffixes['eko'])
        [('y', ('eu_ES',))]
        >>> 'ren' in suffixes
        False
        >>> sorted(suffixes['a'])
        [('d', ('eu_ES',))]

        In ``ja_JP`` (Japanese), year/month/day are followed by "年", "月", and
        "日", respectively. Since our sample format string uses only numeric
        conversion specifiers, we extract all three as valid suffixes for their
        corresponding conversions.

        >>> sorted(suffixes['年'])
        [('y', ('ja_JP',))]
        >>> sorted(suffixes['月'])
        [('m', ('ja_JP',))]
        >>> sorted(suffixes['日'])
        [('d', ('ja_JP',))]
        """
        return self._suffixes

if __name__ == "__main__":
    locale_set = TimeLocaleSet.default()
    patterns = locale_set.keywords

    for pattern, fmts in sorted(patterns.items()):
        #if len(fmts) <= 1:
        #    continue
        print("{!r}:".format(pattern))
        for fmt, locales in sorted(fmts):
            print("- {}: {}".format(fmt, ' '.join(sorted(locales))))
        print()
