import cmd
from percentagent import TimeLocaleSet, DateParser

class TimeShell(cmd.Cmd):
    intro = "Type help or ? to list commands.\n"
    prompt = "(percentagent) "

    def __init__(self, *args, **kwargs):
        super(TimeShell, self).__init__(*args, **kwargs)
        self.parser = DateParser()

    def do_guess(self, arg):
        """Guess the format and locale for a date and/or time string."""
        for fmt, value, locales in self.parser.parse(arg):
            print("format: {!r}".format(fmt))
            print("value: {}".format(value))
            print("locales: {}".format(' '.join(sorted(locales or ["C"]))))
            print()

    def do_exit(self, arg):
        """Exit the shell."""
        return True
    do_EOF = do_exit

if __name__ == "__main__":
    TimeShell().cmdloop()
