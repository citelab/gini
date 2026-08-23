# GINI POX components.
#
# This package holds the controller apps that ship with GINI. They target the
# gar branch of POX (Python 3) speaking OpenFlow 1.0, and they are written for
# the GINI Flow Switch, whose datapath installs a single match-all -> NORMAL
# rule at start-up so that an *uncontrolled* switch still forwards. Every app in
# gini.samples therefore deletes that default rule on ConnectionUp before it
# starts making its own decisions (see the Custom Controllers chapter).
