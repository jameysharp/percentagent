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

    All instance variables are dictionaries which map a string to a set of
    locales in which that string is used.

    Except for :py:attr:`.formats`, the dictionary keys in all instance
    variables are semicolon-separated (``;``) ordered lists. Their semantics
    are documented in :manpage:`locale(5)`.
    """

    def __init__(self, formats=None, day=None, mon=None, am_pm=None, alt_digits=None, era=None):
        locales = _InternTable()

        self.formats = self._compact(locales, formats)
        """Sample format strings to extract prefix and suffix patterns from."""

        self.day = self._compact(locales, day)
        """Names of days of the week."""

        self.mon = self._compact(locales, mon)
        """Names of months."""

        self.am_pm = self._compact(locales, am_pm)
        """Strings indicating times before or after noon."""

        self.alt_digits = self._compact(locales, alt_digits)
        """Numbers from writing systems which do not use Unicode digits."""

        self.era = self._compact(locales, era)
        """Definitions of how years are counted and displayed."""

    _empty_dictionary = {}

    @classmethod
    def _compact(cls, locales, d):
        """
        Ensure that identical locale names are only stored in memory once, and
        similarly for groups of locales.
        """

        if not d:
            return cls._empty_dictionary
        return {
            k: locales(tuple(sorted(map(locales, v))))
            for k, v in d.items()
        }

    @classmethod
    def from_json(cls, f):
        """
        Load a locale set from a JSON-formatted stream, such as one produced by
        ``utils/lc_time.py``.

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

    _equivalents = {
        'e': 'd',
        'I': 'H',
        'k': 'H',
        'l': 'H',
        'y': 'Y',
    }

    _text_keywords = {
        "day": "a",
        "mon": "b",
        "am_pm": "p",
        "alt_digits": "O",
    }

    # Some patterns are common across so many locales that they are useless for
    # guessing which locale the input came from, and should just be allowed for all
    # locales.
    _date_patterns = [ order.format(fmt) for order in ("{}#", "#{}") for fmt in "Ymd" ]
    _global_patterns = {
        ':': ("H#", "M#", "#M", "#S"),
        '/': _date_patterns,
        '-': _date_patterns,
        'utc': ("#z",), # "UTC+hhmm"
    }

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

    fmt_token = re.compile(_ignore + r'%[-_0^#]?\d*[EO]?([a-zA-Z+%])' + _ignore)
    """
    A compiled regular expression to match :manpage:`strftime(3)`-style
    conversion specifiers. This regex contains a single group which returns the
    final conversion specifier character, skipping any flags, field widths, or
    modifiers. The :py:meth:`~re.Pattern.findall` method will return a list of
    just the conversion specifier characters; the :py:meth:`~re.Pattern.split`
    method will return the same but alternating with non-conversion text.
    """

    def extract_patterns(self):
        """
        Return literal strings that indicate what role nearby parts of a date
        or time string play, and in which locales.

        In this example we set up a small locale set with just one example date
        format and one abbreviated weekday name which both share the same
        pattern text:

        >>> from pprint import pprint
        >>> locale_set = TimeLocaleSet(
        ...     formats={'%Y年 %m月 %d日': {'ja_JP'}},
        ...     day={'日': {'cmn_TW', 'ja_JP'}},
        ... )
        >>> patterns = locale_set.extract_patterns()

        Now look up '日' among the extracted patterns:

        >>> pprint(patterns['日'])
        {'a': ('cmn_TW', 'ja_JP'), 'd#': ('ja_JP',)}

        So pattern extraction has found that '日' could be the name of a day of
        the week (``%a`` format) in either the ``cmn_TW`` or ``ja_JP`` locales,
        or it could appear after the day-of-month (``%d`` format) in the
        ``ja_JP`` locale.

        The outer dictionary's keys are literal strings that should be matched
        during format-string inference.

        Inner dictionaries' keys are a single conversion specifier character
        (see :manpage:`strftime(3)`), optionally with a hash-mark (``#``)
        either before or after it.

        * ``#d`` indicates that the string can appear as a prefix of a ``%d``
          conversion.
        * ``a`` indicates that the string can be the result of a ``%a``
          conversion.
        * ``d#`` indicates that the string can appear as a suffix of a ``%d``
          conversion.

        The values in the inner dictionaries are sets of locales where this
        string was found. An empty set indicates that the string may appear in
        any locale.

        :rtype: dict(str, dict(str, tuple(str)))
        """

        patterns = defaultdict(lambda: defaultdict(set))

        # TODO: extract patterns from self.era

        for v, locales in self.formats.items():
            tokens = self.fmt_token.split(v)
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

                fmt = self._equivalents.get(fmt, fmt)
                if prefix != '':
                    patterns[prefix.casefold()]["#" + fmt].update(locales)
                if suffix != '':
                    patterns[suffix.casefold()][fmt + "#"].update(locales)

        for k, fmt in self._text_keywords.items():
            for v, locales in getattr(self, k).items():
                for word in v.split(";"):
                    patterns[word.strip().casefold()][fmt].update(locales)

        for pattern, fmts in self._global_patterns.items():
            patterns[pattern] = dict.fromkeys(fmts, frozenset())

        for fmt, merges in self._merge_patterns:
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
                    patterns[tzname.casefold()]["Z"] = frozenset()

        localesets = _InternTable()

        return {
            pattern: {
                fmt: localesets(tuple(sorted(locales)))
                for fmt, locales in fmts.items()
            }
            for pattern, fmts in patterns.items()
        }

if __name__ == "__main__":
    locale_set = TimeLocaleSet.default()
    patterns = locale_set.extract_patterns()

    for pattern, fmts in sorted(patterns.items()):
        #if len(fmts) <= 1:
        #    continue
        print("{!r}:".format(pattern))
        for fmt, locales in sorted(fmts.items()):
            print("- {}: {}".format(fmt, ' '.join(sorted(locales))))
        print()
