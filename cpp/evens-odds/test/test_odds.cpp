#include <climits> // for INT_MAX and INT_MIN
#include <gtest/gtest.h>

#include "odds.h"

// Demonstrate some basic assertions.
TEST(OddTest, BasicAssertions) {
  // Expect equality.
  EXPECT_EQ(is_odd(1), true);
  EXPECT_EQ(is_odd(43), true);
  EXPECT_EQ(is_odd(9567), true);
  EXPECT_EQ(is_odd(54659), true);
  EXPECT_EQ(is_odd(INT_MAX), true);
  EXPECT_EQ(is_odd(UINT_MAX), true);
  // EXPECT_EQ(is_odd(LONG_MAX), true);
  // EXPECT_EQ(is_odd(ULONG_MAX), true);
  // Expect equality.
  EXPECT_EQ(is_odd(-1), true);
  EXPECT_EQ(is_odd(-57), true);
  EXPECT_EQ(is_odd(INT_MIN + 1), true);
}
