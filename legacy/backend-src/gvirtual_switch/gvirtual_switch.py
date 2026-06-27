#!/usr/bin/python
# -*- coding: utf-8 -*-

import os, sys, subprocess
from cmd import Cmd
import pipes
from inspect import cleandoc


class SwitchDoesNotExist(Exception):
    pass


class GVirtualSwitchShell(Cmd, object):
    """An interactive shell to manage open virtual switches.

    Supported shell commands and their ovs-ofctl respective commands.
        - ovs-ofctl show switch
        - ovs-ofctl add-flow switch flow
        - ovs-ofctl dump-flows switch [flows]
        - ovs-ofctl del-flows switch
        - ovs-ofctl [--strict] del-flows switch [flows]
        - ovs-ofctl [--strict] mod-flows switch flow
    """
    OVS_OFCTL_BASE_COMMANDS = {
        "add": "ovs-ofctl add-flow {{switch_name}} {{arguments}}",
        "del": "ovs-ofctl del-flows {{switch_name}} {{arguments}}",
        "dump": "ovs-ofctl dump-flows {{switch_name}} {{arguments}}",
        "mod": "ovs-ofctl mod-flows {{switch_name}} {{arguments}}",
        "show": "ovs-ofctl show {{switch_name}}"
    }

    def __init__(self, switch_name, *args, **kwargs):
        """Initialize GOpenFlowControlShell"""
        if not self._check_valid_switch(switch_name):
            raise SwitchDoesNotExist()

        # Cmd.__init__(self, *args, **kwargs)
        super(GVirtualSwitchShell, self).__init__(*args, **kwargs)
        self.switch_name = switch_name
        self.cleaned_switch_name = pipes.quote(switch_name)
        self.prompt = "(%s) >> " % switch_name

        # Update switch name in ovs-ofctl commands
        for key, value in self.OVS_OFCTL_BASE_COMMANDS.items():
            self.OVS_OFCTL_BASE_COMMANDS[key] = value.replace(
                "{{switch_name}}",
                self.cleaned_switch_name,
                1
            )

    @staticmethod
    def _check_valid_switch(switch_name):
        """Check if there exists an open virtual switch with switch_name"""
        cleaned_switch_name = pipes.quote(switch_name)
        check_command = "ovs-ofctl show %s" % cleaned_switch_name
        runcmd = subprocess.Popen(
            check_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        runcmd.communicate()
        if runcmd.returncode == 0:
            return True
        else:
            return False

    @staticmethod
    def _clean_command_line(command, arguments=None):
        """Clean up arguments to avoid command injection"""
        arguments = "" if not arguments else arguments
        cleaned_arguments = pipes.quote(arguments)
        command = command.replace("{{arguments}}", cleaned_arguments, 1)
        return command

    def do_exit(self, args):
        """Exit the shell

        USAGE: exit | EOF"""
        self.stdout.write("Bye\n")
        return True

    do_EOF = do_exit

    def do_add(self, args):
        """Add new flow entries to current switch

        USAGE: add flow"""
        command = self._clean_command_line(self.OVS_OFCTL_BASE_COMMANDS["add"], args)
        runcmd = subprocess.Popen(command, shell=True, stdout=self.stdout)
        runcmd.communicate()

    def do_del(self, args):
        """Delete flow entries from current switch's table

        USAGE: del [FLOW]"""
        command = self._clean_command_line(self.OVS_OFCTL_BASE_COMMANDS["del"], args)
        runcmd = subprocess.Popen(command, shell=True, stdout=self.stdout)
        runcmd.communicate()

    def do_dump(self, args):
        """Display the current flow table

        USAGE: dump [FLOW]"""
        command = self._clean_command_line(self.OVS_OFCTL_BASE_COMMANDS["dump"], args)
        runcmd = subprocess.Popen(command, shell=True, stdout=self.stdout)
        runcmd.communicate()

    def do_mod(self, args):
        """Modify the actions in entries from switch's table

        USAGE: mod FLOW"""
        command = self._clean_command_line(self.OVS_OFCTL_BASE_COMMANDS["mod"], args)
        runcmd = subprocess.Popen(command, shell=True, stdout=self.stdout)
        runcmd.communicate()

    def do_show(self, args):
        """Prints information on switch, including information on its flow tables and ports

        USAGE: show"""
        command = self._clean_command_line(self.OVS_OFCTL_BASE_COMMANDS["show"])
        runcmd = subprocess.Popen(command, shell=True, stdout=self.stdout)
        runcmd.communicate()

    def do_help(self, arg):
        """List available commands with "help" or detailed help with "help cmd"."""
        if arg:
            # XXX check arg syntax
            try:
                doc=getattr(self, 'do_' + arg).__doc__
                if doc:
                    self.stdout.write("%s\n" % cleandoc(doc))
                    return
            except AttributeError:
                self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
                return
        else:
            cmds = {}
            for name in self.get_names():
                if name[:3] == "do_":
                    cmd = name[3:]
                    cmds[cmd] = getattr(self, name).__doc__
            self.stdout.write("Interactive shell to manage open virtual switch through ovs-ofctl.\n\n")
            self.stdout.write("List of available commands:\n")
            col_width = max([len(word) for word in cmds.keys()]) + 4
            for key, value in cmds.items():
                self.stdout.write("\t%s%s\n" %
                                  (key.ljust(col_width), value.split("\n")[0]))

    def cmdloop(self, intro=None):
        """Override Cmd.cmdloop to add handler for SIGINT

        Repeatedly issue a prompt, accept input, parse an initial prefix
        off the received input, and dispatch to action methods, passing them
        the remainder of the line as argument.
        """

        self.preloop()
        if self.use_rawinput and self.completekey:
            try:
                import readline
                self.old_completer = readline.get_completer()
                readline.set_completer(self.complete)
                readline.parse_and_bind(self.completekey+": complete")
            except ImportError:
                pass
        try:
            if intro is not None:
                self.intro = intro
            if self.intro:
                self.stdout.write(str(self.intro)+"\n")
            stop = None
            while not stop:
                if self.cmdqueue:
                    line = self.cmdqueue.pop(0)
                else:
                    if self.use_rawinput:
                        try:
                            line = raw_input(self.prompt)
                        except EOFError:
                            line = 'EOF'
                        except KeyboardInterrupt:
                            self.stdout.write("\nKeyboardInterrupt\n")
                            continue
                    else:
                        self.stdout.write(self.prompt)
                        self.stdout.flush()
                        line = self.stdin.readline()
                        if not len(line):
                            line = 'EOF'
                        else:
                            line = line.rstrip('\r\n')
                line = self.precmd(line)
                stop = self.onecmd(line)
                stop = self.postcmd(stop, line)
            self.postloop()
        finally:
            if self.use_rawinput and self.completekey:
                try:
                    import readline
                    readline.set_completer(self.old_completer)
                except ImportError:
                    pass


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("\nUSAGE: python gopenflow_control.py <name of open virtual switch>\n\n")
        exit(1)
    else:
        ovs_name = sys.argv[1]
        shell = None

        try:
            shell = GVirtualSwitchShell(ovs_name)
            shell.cmdloop()
        except SwitchDoesNotExist:
            sys.stderr.write("Switch does not exist.\n")
            exit(1)
