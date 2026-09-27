#include <climits> // for INT_MAX and INT_MIN
#include <gtest/gtest.h>

#include "odds.h"

// Demonstrate some basic assertions.
TEST(EvenTest, BasicAssertions) {
  // Expect non-equality
  EXPECT_NE(is_odd(2), true);
  EXPECT_NE(is_odd(4), true);
  EXPECT_NE(is_odd(-2), true);
  EXPECT_NE(is_odd(INT_MIN), true);
}
