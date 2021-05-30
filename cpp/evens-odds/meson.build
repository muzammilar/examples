# project details
project('evens-odds', 'cpp',
  version : '0.1',
  default_options : ['warning_level=3', 'cpp_std=c++20'])

# include all headers; This `inc` is referred from subdir projects meson.build files
inc = include_directories('include')

# setup gtest. This will be used in `test`
gtest_proj = subproject('gtest')
gtest_dep = gtest_proj.get_variable('gtest_dep')
gmock_dep = gtest_proj.get_variable('gmock_dep')

# meson will try to find a meson.build file inside following directories
subdir('include')
subdir('src')
subdir('test')
