#!/usr/bin/env python

from collections import Counter, OrderedDict, namedtuple
import datetime
import itertools
import re

from percentagent.extract_patterns import TimeLocaleSet

_Assignment = namedtuple("_Assignment", (
    "pos",
    "value",
    "fmt",
    "locales",
    "prefix",
    "suffix",
))

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
        >>> parser.parse("2018-01-09")
        [('%Y-%m-%d', datetime.date(2018, 1, 9), None), ('%Y-%d-%m', datetime.date(2018, 9, 1), None)]

        That output indicates that the input can be explained by either
        year-month-day order or year-day-month order, and there are no hints
        indicating which locale the date was formatted for.

        On the other hand, "13" is too large to be a month number, so this
        example is unambiguous:

        >>> parser.parse("2018-05-13")
        [('%Y-%m-%d', datetime.date(2018, 5, 13), None)]

        Separators are not required between conversions, but they can help with
        ambiguity:

        >>> sorted(fmt for fmt, value, locales in parser.parse("210456"))
        ['%H%M%S', '%d%m%y']
        >>> parser.parse("21-04-56")
        [('%d-%m-%y', datetime.date(2056, 4, 21), None)]
        >>> parser.parse("21:04:56")
        [('%H:%M:%S', datetime.time(21, 4, 56), None)]

        Using locale-specific strings can help avoid ambiguity too:

        >>> parser = DateParser(TimeLocaleSet(
        ...     mon={"Jan;Feb;Mar;Apr;May;Jun;Jul;Aug;Sep;Oct;Nov;Dec": ["en_US"]},
        ... ))
        >>> parser.parse("2018Jan9")
        [('%Y%b%d', datetime.date(2018, 1, 9), frozenset({'en_US'}))]

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
        keywords = map(self._lookup_keyword, raw)

        groups = _DateTime(**{ field: [] for field in _DateTime._fields })
        choices_per_position = {}
        always_literal = set()
        for idx, (keyword, prefix, suffix) in enumerate(zip(keywords, prefixes, suffixes)):
            if "y" in prefix:
                prefix["C"] = tuple(set(prefix["y"] + prefix.get("C", ())))
            if not keyword:
                always_literal.add(idx)
            else:
                choices_per_position[idx] = len(keyword)
                for fmt, value, locales in keyword:
                    category = fmt[-1]
                    if category == "b":
                        # Month-names should be treated like numeric months.
                        category = "m"
                    elif category == "z":
                        category = "Z"
                    getattr(groups, category).append(_Assignment(
                        fmt=fmt,
                        pos=idx,
                        value=value,
                        locales=locales,
                        prefix=prefix.get(fmt[-1]),
                        suffix=suffix.get(fmt[-1]),
                    ))

        # If a required date field is unsatisfiable, this is not a date.
        if not all(getattr(groups, category) for category in _State._min_date_formats):
            for category in _State._all_date_formats:
                getattr(groups, category).clear()

        # If a required time field is unsatisfiable, this is not a time.
        if not all(getattr(groups, category) for category in _State._min_time_formats):
            for category in _State._all_time_formats:
                getattr(groups, category).clear()

        for group in groups:
            group.sort(key=lambda assignment: (
                -self._optimistic_score(assignment),
                choices_per_position[assignment.pos],
            ))

        required_formats = _State._min_date_formats + _State._min_time_formats
        groups = OrderedDict(sorted(
            (
                (
                    category,
                    (
                        group,
                        tuple(
                            (f, required)
                            for f, required in _position_constraints
                            if category in required
                        ),
                        tuple(
                            (f, required)
                            for f, required, revisit in _value_constraints
                            if category in required or category in revisit
                        ),
                    )
                )
                for category, group in zip(groups._fields, groups)
                if group
            ),
            key=lambda i: (i[0] not in required_formats, len(i[1][0]))
        ))

        # We've already filtered out all possibilities; there's nothing here.
        if not groups:
            return []

        constrained_groups = []
        while groups:
            category, (group, position, value) = groups.popitem(last=False)
            constrained_groups.append((category, group, position, value))
            required = frozenset(itertools.chain.from_iterable(required for f, required in itertools.chain(position, value)))
            if required:
                required = [
                    category
                    for category in reversed(groups.keys())
                    if category in required
                ]
                for category in required:
                    groups.move_to_end(category, last=False)
        groups = constrained_groups

        best_quality = 0
        best_candidates = []

        partials = [
            _State.empty._replace(
                unconverted=frozenset(always_literal),
                remaining_groups=tuple(groups),
            ).children()
        ]
        while partials:
            try:
                quality, locales, state = next(partials[-1])
            except StopIteration:
                partials.pop()
                continue

            if state.remaining_groups:
                # Admissable heuristic: compute the best score each group
                # could possibly achieve. Don't count conversion specifiers
                # that we've already used, but don't worry about conflicts
                # in the groups we haven't assigned yet. Any such conflicts
                # can only reduce the resulting score, and we only need to
                # make sure that the heuristic is at least as large as the
                # true value of the best leaf in this subtree. However, the
                # more precise we can be here, the fewer nodes we have to
                # search, so we can spend some CPU time on precision and
                # still come out ahead.
                assigned = state.unconverted.union(state.pos).difference((None,))
                heuristic = len(state.pending_hints) + sum(
                    next((
                        self._optimistic_score(assignment)
                        for assignment in group[1]
                        if assignment.pos not in assigned
                    ), 0)
                    for group in state.remaining_groups
                )

                if quality + heuristic < best_quality:
                    # Even assuming the remaining groups get the highest
                    # possible score, this state is still not good enough.
                    continue

                partials.append(state.children())
                continue

            value = state.valid()
            if value is None:
                continue

            quality, locales, state = state.final_score()

            if best_quality is not None and quality < best_quality:
                # We've seen better, so skip this one.
                continue

            if quality != best_quality:
                best_quality = quality
                best_candidates = []

            conversions = dict(zip(state.pos, state.fmts))
            fmts = [ conversions.get(idx) or literal for idx, literal in enumerate(raw) ]

            pattern = ''.join(lit + fmt for lit, fmt in zip(literals, fmts + [''])).replace("%C%y", "%Y")
            best_candidates.append((pattern, value, locales))
        return best_candidates

    def _lookup_keyword(self, raw):
        keyword = raw.casefold()
        found = self.locale_set.keywords.get(keyword)
        if found:
            ret = []
            for fmt, value, locales in found:
                locales = frozenset(locales)
                if fmt == "O":
                    ret.extend(self._legal_number("%O", value, locales))
                else:
                    ret.append(("%" + fmt, value, locales))
            return tuple(ret)
        if keyword[0] in "+-":
            if keyword[1:].isdigit():
                return (("%z", keyword, None),)
        elif keyword.isdigit():
            return tuple(self._legal_number("%", int(keyword), None))
        return ()

    @staticmethod
    def _legal_number(prefix, value, locales):
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
        return ((prefix + fmt, value, locales) for fmt in legal)

    @staticmethod
    def _optimistic_score(assignment):
        return 1 + (assignment.prefix is not None) + (assignment.suffix is not None)

_position_constraints = []

def month_near_day(pos):
    if pos.m < pos.d:
        return range(pos.m + 1, pos.d)
    else:
        return range(pos.d + 1, pos.m)
_position_constraints.append((month_near_day, "md"))

def century_then_year(pos):
    if pos.C + 1 != pos.y:
        return None
    return ()
_position_constraints.append((century_then_year, "Cy"))

def hour_then_minute(pos):
    if pos.H > pos.M:
        return None
    return range(pos.H + 1, pos.M)
_position_constraints.append((hour_then_minute, "HM"))

def minute_then_second(pos):
    if pos.M > pos.S:
        return None
    return range(pos.M + 1, pos.S)
_position_constraints.append((minute_then_second, "MS"))

_value_constraints = []

def valid_day_of_month(value):
    """
    Compute an upper bound on month-length. Even if we haven't identified all
    the fields needed for checking leap-years yet, we can still prune if
    value.d is bigger than the month can ever possibly be.
    """
    if value.m == 2:
        month_length = 29
        if value.y is not None:
            if (value.y % 4) != 0:
                month_length = 28
            elif value.y == 0 and value.C is not None and (value.C % 4) != 0:
                month_length = 28
    else:
        # Odd-numbered months are longer through July, then even-numbered
        # months are longer for the rest of the year.
        month_length = 30 + ((value.m % 2) == (value.m < 8))
    return value.d <= month_length
_value_constraints.append((valid_day_of_month, "md", "Cy"))

def valid_12_hour_clock(value):
    return 1 <= value.H <= 12
_value_constraints.append((valid_12_hour_clock, "Hp", ""))

_DateTime = namedtuple("_DateTime", list("CymdaHMSpZ"))
_DateTime.empty = _DateTime(**dict.fromkeys(_DateTime._fields, None))

class _State(namedtuple("_State", (
        "remaining_groups",
        "date_present",
        "time_present",
        "unconverted",
        "pos",
        "value",
        "fmts",
        "required_locales",
        "pending_hints",
        "satisfied",
        "globally_satisfied",
    ))):
    __slots__ = ()

    def children(self):
        category, options, position_constraints, value_constraints = self.remaining_groups[0]
        remaining_groups = self.remaining_groups[1:]

        date_present = self.date_present
        time_present = self.time_present
        if category in self._all_date_formats:
            date_present = True
        else:
            time_present = True

        position_constraints = [
            f
            for f, required in position_constraints
            if all(
                (c == category) == (getattr(self.pos, c) is None)
                for c in required
            )
        ]

        value_constraints = [
            f
            for f, required in value_constraints
            if all(
                (c == category) == (getattr(self.pos, c) is None)
                for c in required
            )
        ]

        for assignment in options:
            if assignment.pos in self.unconverted or assignment.pos in self.pos:
                continue

            if assignment.locales and self.required_locales:
                locales = assignment.locales.intersection(self.required_locales)
                if not locales:
                    continue
            else:
                locales = assignment.locales or self.required_locales

            if assignment.pos - 1 == self.pos.C and category != "y":
                continue

            pos = self.pos._replace(**{category: assignment.pos})
            if position_constraints:
                exclude = [constraint(pos) for constraint in position_constraints]
                if None in exclude:
                    continue
                exclude = frozenset(itertools.chain.from_iterable(exclude))
                if not exclude.isdisjoint(pos):
                    continue
            else:
                exclude = ()

            value = self.value._replace(**{category: assignment.value})
            if not all(constraint(value) for constraint in value_constraints):
                continue

            fmts = self.fmts._replace(**{category: assignment.fmt})

            pending_hints = list(self.pending_hints)
            pending_hints.append((None, ()))
            if assignment.prefix is not None:
                pending_hints.append((assignment.pos - 1, assignment.prefix))
            if assignment.suffix is not None:
                pending_hints.append((assignment.pos + 1, assignment.suffix))

            if exclude:
                exclude = self.unconverted.union(exclude)
            else:
                exclude = self.unconverted

            satisfied = self.satisfied
            globally_satisfied = self.globally_satisfied
            deferred_hints = []
            for idx, hint in pending_hints:
                if idx is None or idx in exclude:
                    if hint:
                        satisfied = satisfied.copy()
                        satisfied.update(hint)
                    else:
                        globally_satisfied += 1
                elif idx not in pos:
                    # Save this hint until we decide this index.
                    deferred_hints.append((idx, hint))

            new = _State(
                remaining_groups=remaining_groups,
                date_present=date_present,
                time_present=time_present,
                unconverted=exclude,
                pos=pos,
                value=value,
                fmts=fmts,
                required_locales=locales,
                pending_hints=tuple(deferred_hints),
                satisfied=satisfied,
                globally_satisfied=globally_satisfied,
            )

            yield new.score()

        # Also allow skipping this category entirely:
        if category in self._min_date_formats:
            if self.date_present:
                # We already committed to a date field in this subtree, so we
                # can't skip this mandatory one.
                return
            # Now that we're skipping a required date field, we can't pick
            # any date fields in this subtree.
            remaining_groups = tuple(
                group
                for group in remaining_groups
                if group[0] not in self._all_date_formats
            )
        elif category in self._min_time_formats:
            if self.time_present:
                # We already committed to a time field in this subtree, so we
                # can't skip this mandatory one.
                return
            # Now that we're skipping a required time field, we can't pick
            # any time fields in this subtree.
            remaining_groups = tuple(
                group
                for group in remaining_groups
                if group[0] not in self._all_time_formats
            )

        new = self._replace(remaining_groups=remaining_groups)
        yield new.score()

    def final_score(self):
        new = self
        if self.pending_hints:
            satisfied = self.satisfied.copy()
            globally_satisfied = self.globally_satisfied
            for idx, hint in self.pending_hints:
                if hint:
                    satisfied.update(hint)
                else:
                    globally_satisfied += 1
            new = self._replace(satisfied=satisfied, globally_satisfied=globally_satisfied)
        return new.score()

    def valid(self):
        d = None
        if self.date_present:
            # TODO: disambiguate missing century around a configurable date
            if self.value.C is not None:
                centuries = (self.value.C,)
            elif self.value.y == 0 and self.value.m == 2 and self.value.d == 29:
                # Among years divisible by 100, only those that are also
                # divisible by 400 are leap years. So 2000 is the only nearby
                # year that could work in this case.
                centuries = (20,)
            elif self.value.a is not None:
                # If we know the weekday, a two-digit year is unambiguous
                # within a four-century window. Let's just guess in a window
                # around the 20th/21st centuries.
                centuries = (20, 19, 21, 18)
            else:
                # If all else fails, use the current POSIX rule for how
                # strptime interprets two-digit years.
                if self.value.y <= 68:
                    centuries = (20,)
                else:
                    centuries = (19,)

            for C in centuries:
                d = datetime.date(C * 100 + self.value.y, self.value.m, self.value.d)
                if self.value.a is None or (d.weekday() + 1) % 7 == self.value.a:
                    break
            else:
                return None

        t = None
        if self.time_present:
            H = self.value.H
            if self.value.p is not None:
                # 12am is 00:00, and 12pm is 12:00
                H = (H % 12) + 12 * self.value.p
            t = datetime.time(H, self.value.M, self.value.S or 0)

        if d and t:
            return datetime.datetime.combine(d, t)

        return d or t

    def score(self):
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
        return self.globally_satisfied + locally_satisfied, satisfied_locales, self

    _min_date_formats = "ymd"
    _all_date_formats = _min_date_formats + "Ca"
    _min_time_formats = "HM"
    _all_time_formats = _min_time_formats + "SpZ"

_State.empty = _State(
    remaining_groups=(),
    date_present=False,
    time_present=False,
    unconverted=frozenset(),
    pos=_DateTime.empty,
    value=_DateTime.empty,
    fmts=_DateTime.empty,
    required_locales=None,
    pending_hints=(),
    satisfied=Counter(),
    globally_satisfied=0,
)

if __name__ == "__main__":
    import time
    import timeit
    def perf(f, repeat, number):
        #return ()
        timer = timeit.Timer('f()', timer=time.process_time, globals={'f': f})
        perf = min(timer.repeat(repeat=repeat, number=number)) / number
        print("{:.2f}ms".format(1000 * perf))
        return [perf]

    locale_set = TimeLocaleSet.default()
    perf(TimeLocaleSet.default, 5, 1)

    parser = DateParser(locale_set)
    perf(lambda: DateParser(locale_set), 5, 1)

    examples = (
        "5/6/2018, 4:45:18 AM",
        "20180506T114518Z",
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
    times = []
    for example in examples:
        print(repr(example))
        for fmt, value, locales in parser.parse(example):
            print("- {!r} = {} ({})".format(fmt, value, ' '.join(sorted(locales or ["C"]))))
        times.extend(perf(lambda: parser.parse(example), 10, 3))
        print()

    times.sort()
    print(" ".join("{:.2f}ms".format(1000 * time) for time in times))
