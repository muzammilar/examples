/*
 * odds.cpp
 *      Author: Muzammil
 */

#include "odds.h"

bool is_odd(int integer) {
  return integer & 1; // using integer%2 doesn't work for -ve numbers
}
