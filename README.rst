Guess strftime format strings
=============================

.. image:: https://readthedocs.org/projects/percentagent/badge/?version=latest
    :target: https://percentagent.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

https://github.com/jameysharp/percentagent

This resembles and is inspired by the ``parser`` module of `dateutil`_,
in that it attempts to parse structured dates and times out of arbitrary
strings. However this implementation has several advantages:

.. _dateutil: https://pypi.org/project/python-dateutil/

- Infers :manpage:`strftime(3)`-style format strings
- Returns all ambiguous parses
- Supports many languages and locales out of the box
- Reports which locales could have produced the input
- Additional locales or parsing hints can be provided by example

You should use ``dateutil`` instead if you don't need any of those
features, because this library also has some disadvantages (although
these may get fixed over time):

- No test suite yet, while ``dateutil`` is well-tested
- This library takes a few milliseconds to parse one input, while
  ``dateutil`` takes a few hundred microseconds
- Timezone offsets and abbreviations are recognized but not yet reported
  out of the library

Format strings
--------------

This library returns :manpage:`strftime(3)`-style format strings, as
well as the corresponding :py:class:`~datetime.datetime` object (or a
:py:class:`~datetime.date` or :py:class:`~datetime.time` if that's all
that could be found in the input) for each possible format string.

The format strings are useful if you have several different examples of
strings produced by a single, unknown, format string. A single date/time
string may be ambiguous (such as "y/m/d" versus "y/d/m"). But odds are
good that if you see a few more samples in the same format, only one
format string will explain all of them.

Ambiguous inputs
----------------

If an input string is ambiguous, such as when it's unclear whether a
date uses day/month or month/day order, this library returns all
possibilities. You can implement your own heuristics to decide which one
is best for your application.

By contrast, ``dateutil`` picks one interpretation, and provides options
letting you guide which one it will pick.

Broad locale support
--------------------

This library has a fair shot at handling dates in a wide range of
languages, without any configuration.

I've extracted comprehensive data about how dates are formatted around
the world from the GNU C library locale database. The script which does
that is ``utils/lc_time`` if you want to run it on your own system with
a POSIX-conforming implementation of :manpage:`locale(1)`. If your
system's locale database includes locales or format-string examples that
glibc doesn't, we can merge the extracted data to make this library
support even more kinds of input.

This library will also tell you which locales could have been used to
produce the input you hand it. That gives you an additional data point
if you're comparing different date strings to determine if they were
generated using the same format. You could also use the suggested
locales as a hint about the language of the surrounding text, or the
most likely timezones used in the locale's primary country.

Command-line usage
==================

A simple interactive interface is available::

    python -m percentagent

License
=======

`BSD-2-Clause <https://spdx.org/licenses/BSD-2-Clause.html>`_

This repository includes ``glibc.json``, a file produced by extracting
selected data from the GNU C Library locale database. The source files
for that database include the following text:

  This file is part of the GNU C Library and contains locale data.
  The Free Software Foundation does not claim any copyright interest
  in the locale data contained in this file.  The foregoing does not
  affect the license of the GNU C Library as a whole.  It does not
  exempt you from the conditions of the license if your use would
  otherwise be governed by that license.

Therefore, I believe the derived data is not subject to the license of
the GNU C Library. To be clear, I also do not claim any copyright
interest in the locale facts in the above file.
