# Shared library
libodd = shared_library(
  'oddchecker', # library name
  'odds.cpp', # source files to be compiled
  include_directories : inc, # previously declared include directories in root `meson.build`. This simplifies relative paths
  install : true) # `libodd` will be part of project installation

# Static library
libodd_static = static_library(
  'odd', # library name
  'odds.cpp', # source files to be compiled
  include_directories : inc, # previously declared include directories in root `meson.build`. This simplifies relative paths
  install : true) # `libodd` will be part of project installation

# Executable
oddexecutable = executable('oddnumbers', # executable binary
  'main.cpp',  # source files
  include_directories : inc, # headers to include. Declared in root `meson.build`
  link_with : libodd_static, # linking executable with previously declared static library `libodd_static`. Note: static linking should be avoided for large projects.
  install : true)
