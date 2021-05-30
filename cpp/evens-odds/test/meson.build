
# all test files to be added
tests_src = [
  'gtest_all.cpp',
  'test_evens.cpp',
  'test_odds.cpp',
]

# Test Shared Library
test_libodd = executable(
  'gtest-all',
  tests_src,
  dependencies : [
    gtest_dep,  # from root `meson.build`
    gmock_dep], # from root `meson.build`
  include_directories : inc, # headers to include. Declared in root `meson.build`
  link_with : libodd,  # from `src` direcctory's `meson.build`
)

# Test Shared Library
test_libodd_static = executable(
  'gtest-all-static',
  tests_src,
  dependencies : [
    gtest_dep,  # from root `meson.build`
    gmock_dep], # from root `meson.build`
  include_directories : inc, # headers to include. Declared in root `meson.build`
  link_with : libodd_static,  # from `src` direcctory's `meson.build`
)

# Tests
test('gtest test - shared library', test_libodd)
test('gtest test - static object', test_libodd_static)
