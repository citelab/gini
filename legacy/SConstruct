# -*- python -*-
#
# GINI Version 2.2
# (C) Copyright 2009, McGill University
#
# Scons compile script for creating GINI installation
#
import os,os.path,stat
import shutil
import sys
import py_compile
from SCons.Node import FS
from subprocess import call

# Make sure git submodules are initialized
# call(["git", "submodule", "update", "--init", "--recursive"])

# import SconsBuilder

######################
# Shared Directories #
######################

src_dir = os.getcwd()

prefix = ARGUMENTS.get('PREFIX',"")
prefix = os.path.realpath(ARGUMENTS.get('DESTDIR',src_dir)) + prefix

build_dir = src_dir + "/build"

etc_dir = prefix + "/etc"
bin_dir = prefix + "/bin"
lib_dir = prefix + "/lib/gini"
sharedir = prefix + "/share/gini"

# gini_src = os.getcwd()
# Export('gini_src')


###############
# Environment #
###############


env = Environment()

env.Clean(build_dir,build_dir)
env.Clean(bin_dir, bin_dir)
env.Clean(sharedir,sharedir)
env.Clean(sharedir,prefix + "/share")
env.Clean(lib_dir, lib_dir)
env.Clean(lib_dir, prefix + "/lib")


##################
# helper methods #
##################


def post_chmod(target):
    env.AddPostAction(target, "chmod +x " + target)


def actually_compile_python(target,source,env):
    py_compile.compile(source[0].abspath)


def compile_python(env, source, alias=None):
    env.Command(source + "c", source , actually_compile_python)
    if alias:
        env.Alias(alias,source + "c")


#####################
# Source Generators #
#####################


def gen_environment_file(target, source, env):
    output_file = open(target[0].abspath,'w')
    output_file.write('#!/usr/bin/python2\n')
    output_file.write('import os, subprocess, sys\n\n')
    output_file.write('previous_dir = os.getcwd()\n')
    output_file.write('os.chdir(os.path.dirname(os.path.realpath(__file__)))\n')
    output_file.write('os.environ["GINI_ROOT"] = os.path.realpath("%s")\n' % os.path.relpath(prefix, bin_dir))
    output_file.write('os.environ["GINI_SHARE"] = os.path.realpath("%s")\n' % os.path.relpath(sharedir, bin_dir))
    output_file.write('os.environ["GINI_LIB"] = os.path.realpath("%s")\n' % os.path.relpath(lib_dir, bin_dir))
    output_file.write('os.environ["GINI_HOME"] = os.environ["HOME"] + "/.gini"\n')
    output_file.write('if not os.path.exists(os.environ["GINI_HOME"] + "/etc"): os.makedirs(os.environ["GINI_HOME"] + "/etc")\n')
    output_file.write('if not os.path.exists(os.environ["GINI_HOME"] + "/sav"): os.makedirs(os.environ["GINI_HOME"] + "/sav")\n')
    output_file.write('if not os.path.exists(os.environ["GINI_HOME"] + "/data"): os.makedirs(os.environ["GINI_HOME"] + "/data")\n')
    output_file.write('if not os.path.exists(os.environ["GINI_HOME"] + "/tmp"): os.makedirs(os.environ["GINI_HOME"] + "/tmp")\n')
    output_file.write('params = [os.path.realpath("%s")]\n' % os.path.relpath(source[0].abspath, bin_dir))
    output_file.write('if len(sys.argv) > 1: params.extend(sys.argv[1:])\n')
    output_file.write('os.chdir(previous_dir)\n')
    output_file.write('os.execv(params[0],params)\n')
    return None


gen_environment_file_builder = Builder(action=gen_environment_file,
                                       single_target=True,
                                       single_source=True,
                                       target_factory=FS.File,
                                       source_factory=FS.File)


def gen_python_path_file(target, source, env):
    output_file = open(target[0].abspath, 'w')
    output_file.write('import os\n')
    output_file.write('GINI_ROOT = "%s"\n' % prefix)
    # if env['PLATFORM'] != 'win32':
    # output_file.write('GINI_HOME = os.environ["HOME"] + "/.gini"\n')
    # else:
    # output_file.write('GINI_HOME = os.environ["USERPROFILE"] + "/gini_files"\n')
    output_file.write('GINI_HOME = "%s"\n' % prefix)
    output_file.close()
    return None


gen_python_path_builder = Builder(action=gen_python_path_file,
                                  single_target=True,
                                  target_factory = FS.File)

env.Append(BUILDERS={'PythonPathFile': gen_python_path_builder})
env.Append(BUILDERS={'PythonEnvFile': gen_environment_file_builder})


################
# Symlink Code #
################


def symlink(target, source, env):
    lnk = target[0].abspath
    src = source[0].abspath
    lnkdir,lnkname = os.path.split(lnk)
    srcrel = os.path.relpath(src,lnkdir)

    if int(env.get('verbose',0)) > 4:
        print 'target:', target
        print 'source:', source
        print 'lnk:', lnk
        print 'src:', src
        print 'lnkdir,lnkname:', lnkdir, lnkname
        print 'srcrel:', srcrel

    if int(env.get('verbose',0)) > 4:
        print 'in directory: %s' % os.path.relpath(lnkdir,env.Dir('#').abspath)
        print '    symlink: %s -> %s' % (lnkname,srcrel)

    try:
        os.symlink(srcrel,lnk)
    except AttributeError:
        # no symlink available, so we make a (deep) copy? (or pass)
        #os.copytree(srcrel,lnk)
        print 'no os.symlink capability on this system?'

    return None


def symlink_emitter(target,source,env):
    """
    This emitter removes the link if the source file name has changed
    since scons does not seem to catch this case.
    """
    lnk = target[0].abspath
    src = source[0].abspath
    lnkdir,lnkname = os.path.split(lnk)
    srcrel = os.path.relpath(src,lnkdir)

    if int(env.get('verbose',0)) > 3:
        ldir = os.path.relpath(lnkdir,env.Dir('#').abspath)
    if lnkdir[:2] == '..':
        ldir = os.path.abspath(ldir)
        print '  symbolic link in directory: %s' % ldir
        print '      %s -> %s' % (lnkname,srcrel)

    try:
        if os.path.exists(lnk):
            if os.readlink(lnk) != srcrel:
                os.remove(lnk)
    except AttributeError:
        # no symlink available, so we remove the whole tree? (or pass)
        #os.rmtree(lnk)
        print 'no os.symlink capability on this system?'

    return target, source


symlink_builder = Builder(action = symlink,
                          target_factory = FS.File,
                          source_factory = FS.Entry,
                          single_target = True,
                          single_source = True,
                          emitter = symlink_emitter)

env.Append(BUILDERS={'Symlink': symlink_builder})


##########################
# Recursive installation #
##########################


def recursive_install(target, source, env):
    targets = []
    for root, dirnames, filenames in os.walk(source):
        for filename in filenames:
            tgt = env.Install(os.path.join(
                target, os.path.relpath(root, os.path.dirname(source))),
                os.path.join(root, filename))
            targets.append(tgt)
    return targets


##################
# Library checks #
##################


conf = Configure(env)
if not conf.CheckLib('readline'):
    print 'Did not find libreadline.so or readline.lib, exiting!'
    Exit(1)
if not conf.CheckLib('pthread'):
    print 'Did not find libpthread.so or pthread.lib, exiting!'
    Exit(1)
env = conf.Finish()

Export('env')


###########
# Backend #
###########


backend_dir = src_dir + "/backend"


#######
# POX #
#######


pox_dir = src_dir + "/backend/third-party/pox"
pox_ext_dir = src_dir + "/backend/src/pox/ext"
lib_pox_dir = lib_dir + "/pox"

pox_targets = recursive_install(lib_dir, pox_dir, env)

gpox = env.Symlink(bin_dir + "/gpox", lib_pox_dir + "/pox.py")
for target in pox_targets:
    env.Depends(gpox, target)

recursive_install(lib_pox_dir, pox_ext_dir, env)


###########
# grouter #
###########


grouter_include = backend_dir + '/include'
grouter_dir = backend_dir + '/src/grouter'
grouter_build_dir = src_dir + '/build/release/grouter'

VariantDir(grouter_build_dir,grouter_dir, duplicate=0)

grouter_env = Environment(CPPPATH=grouter_include)
grouter_env.Append(CFLAGS='-g')
grouter_env.Append(CFLAGS='-DHAVE_PTHREAD_RWLOCK=1')
grouter_env.Append(CFLAGS='-DHAVE_GETOPT_LONG')

# some of the following library dependencies can be removed?
# may be the termcap is not needed anymore..?
# TODO: libslack should be removed.. required routines should be custom compiled

grouter_libs = Split ("""readline
                         termcap
                         slack
                         pthread
                         util
                         m""")

grouter_test_objects = []
grouter_other_objects = []
for file in os.listdir(grouter_dir):
    if file.endswith(".c"):
        if file == "cli.c" or file == "grouter.c":
            grouter_other_objects.append(grouter_env.Object(grouter_build_dir + "/" + file))
        else:
            grouter_test_objects.append(grouter_env.Object(grouter_build_dir + "/" + file))

grouter = grouter_env.Program(grouter_build_dir + "/grouter", grouter_test_objects + grouter_other_objects, LIBS=grouter_libs)

env.Install(lib_dir + "/grouter/", grouter)
post_chmod(lib_dir + "/grouter/grouter")
env.PythonEnvFile(bin_dir + "/grouter", lib_dir + "/grouter/grouter")
post_chmod(bin_dir + "/grouter")

env.Install(sharedir + '/grouter/helpdefs', Glob(grouter_include + '/helpdefs/*'))

env.Alias('install-grouter', bin_dir + '/grouter')
env.Alias('install-grouter',sharedir + '/grouter/helpdefs')
env.Clean(bin_dir + "/grouter", lib_dir + "/grouter/grouter")
env.Clean("install-grouter", lib_dir + "/grouter")
env.Alias('install','install-grouter')


###########
# Gloader #
###########


gloader_dir = backend_dir + "/src/gloader"
gloader_conf = gloader_dir + "/gloader.dtd"
gloader_lib_dir = lib_dir

env.Install(sharedir + "/gloader/", gloader_conf)

result = env.Install(gloader_lib_dir + '/gloader', Glob(gloader_dir + "/*.py"))

for file in Glob(gloader_lib_dir + '/gloader/*.py'):
    compile_python(env,file.abspath,"install-gloader")
env.Clean(gloader_lib_dir + "/gloader",gloader_lib_dir + "/gloader")
post_chmod(gloader_lib_dir + "/gloader/gloader.py")
post_chmod(gloader_lib_dir + "/gloader/gserver.py")

env.PythonEnvFile(bin_dir + '/gserver', gloader_lib_dir + '/gloader/gserver.py')
post_chmod(bin_dir + '/gserver')
env.PythonEnvFile(bin_dir + '/gloader', gloader_lib_dir + "/gloader/gloader.py")
post_chmod(bin_dir + '/gloader')

env.Alias('install-gloader', sharedir + '/gloader')
env.Alias('install-gloader', gloader_lib_dir + '/gloader')
env.Alias('install-gloader', bin_dir + '/gloader')
env.Alias('install-gloader', bin_dir + '/gserver')
env.Alias('install-gloader', sharedir + '/gloader' + '/gloader.dtd')
env.Alias('install','install-gloader')


#########################
#  GVirtualSwitchShell  #
#########################


gvirtual_switch_dir = backend_dir + "/src/gvirtual_switch"
gvirtual_switch_lib_dir = lib_dir

result = env.Install(gvirtual_switch_lib_dir + "/gvirtual_switch", Glob(gvirtual_switch_dir + "/*.py"))

for file in Glob(gvirtual_switch_lib_dir + "gvirtual_switch/*.py"):
    compile_python(env, file.abspath, "install-gvirtual_switch")
env.Clean(gvirtual_switch_lib_dir + "/gvirtual_switch", gvirtual_switch_lib_dir + "/gvirtual_switch")
post_chmod(gvirtual_switch_lib_dir + "/gvirtual_switch/gvirtual_switch.py")

env.PythonEnvFile(bin_dir + "/gvirtual-switch", gvirtual_switch_lib_dir + "/gvirtual_switch/gvirtual_switch.py")
post_chmod(bin_dir + "/gvirtual-switch")

env.Alias("install-gvirtual-switch", gvirtual_switch_lib_dir + "/gvirtual_switch")
env.Alias("install-gvirtual-switch", bin_dir + "/gvirtual-switch")
env.Alias("install", "install-gvirtual-switch")


############
# Frontend #
############


frontend_dir = src_dir + "/frontend"

faq = '/doc/FAQ.html'

env.Install(prefix + '/doc', frontend_dir + faq)
env.Alias('install-doc', prefix + '/doc')
env.Clean(prefix + '/doc',prefix + '/doc')
env.Alias('install','install-doc')


############
# GBuilder #
############


gbuilder_dir = frontend_dir + "/src/gbuilder"

gbuilder_folders = Split("""
    Core
    Devices
    Network
    UI
    Wireless
    Core/utils""")

gbuilder_images = gbuilder_dir + "/images/*"

env.Install(lib_dir + '/gbuilder/', gbuilder_dir + '/gbuilder.py')
compile_python(env, lib_dir + '/gbuilder/gbuilder.py', "install-gbuilder")

# Install each of the gbuilder folders
for x in gbuilder_folders:
    env.Install(lib_dir + '/gbuilder/' + x, Glob(gbuilder_dir + "/" + x + "/*.py"))
    for file in Glob(lib_dir + '/gbuilder/' + x + '/*.py'):
        compile_python(env,file.abspath,"install-gbuilder")
    env.Clean(lib_dir + "/gbuilder", lib_dir + "/gbuilder/" + x)
post_chmod(lib_dir + '/gbuilder/gbuilder.py')
# Install images
env.Install(sharedir + '/gbuilder/images/', Glob(gbuilder_images))
env.Clean(sharedir + '/gbuilder/images/',sharedir + '/gbuilder/images/')

if env['PLATFORM'] != 'win32':
    # env.SymLink(bin_dir + '/gbuilder', sharedir + '/gbuilder/gbuilder.py')
    env.PythonEnvFile(bin_dir + '/gbuilder', lib_dir + '/gbuilder/gbuilder.py')
    env.AddPostAction(bin_dir + '/gbuilder', "chmod +x " + bin_dir + '/gbuilder')
env.Alias('install-gbuilder', bin_dir + '/gbuilder')

env.Alias('install-gbuilder', sharedir + '/gbuilder')
env.Alias('install-gbuilder', lib_dir + '/gbuilder')
env.Clean(sharedir + '/gbuilder',sharedir + '/gbuilder')
env.Clean(lib_dir + '/gbuilder', lib_dir + '/gbuilder')
env.Alias('install', 'install-gbuilder')


##################
# Test Framework #
##################


test_dir = backend_dir + '/tests'
test_build_dir = src_dir + '/build/tests'
test_include = src_dir + '/backend/third-party/mut'
testenv = env.Clone()
testenv.Append(CPPPATH=[test_include])
testenv.Append(CFLAGS='-g')
testenv.Append(CFLAGS='-DHAVE_PTHREAD_RWLOCK=1')
testenv.Append(CFLAGS='-DHAVE_GETOPT_LONG')
testenv.VariantDir(test_build_dir, test_dir, duplicate=0)
tests = []

grouter_test_dir = test_dir + '/grouter'
grouter_test_build_dir = test_build_dir + '/grouter'
grouter_test_env = testenv.Clone()
grouter_test_env.Append(CPPPATH=[grouter_include])
grouter_test_env.VariantDir(grouter_test_build_dir, grouter_test_dir, duplicate=0)

for file in os.listdir(grouter_test_dir):
    if file.endswith("_t.c"):
        tests.append(grouter_test_env.Program(
            os.path.join(grouter_test_build_dir, file[:-2]),
            [os.path.join(grouter_test_build_dir, file)] + grouter_test_objects,
            LIBS=grouter_libs))

test_alias = Alias('test', tests, [test[0].abspath for test in tests])
AlwaysBuild(test_alias)
