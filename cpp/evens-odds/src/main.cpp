/*
 * main.cpp
 *      Author: Muzammil
 */

#include <chrono>   // std::chrono::seconds
#include <climits>  // INT_MIN, INT_MAX
#include <iostream> // std:cout
#include <random>   // std::random_device, std::mt19937
#include <thread>   // std::this_thread::sleep_for

#include "odds.h"

int main() {
  // random seed
  std::random_device random_device;     // create object for seeding
  std::mt19937 engine{random_device()}; // create a random number engine and seed it
  std::uniform_int_distribution<> uniform_distribution(INT_MIN,
                                                       INT_MAX); // create distribution for integers with [1; 9] range

  // Generate a new random number every 5 seconds and check if it's even or odd.
  while (true) {
    auto random_number = uniform_distribution(engine);
    std::cout << random_number << " is an " << (is_odd(random_number) ? "odd" : "even") << " number\n";

    std::this_thread::sleep_for(std::chrono::seconds(5));
  }
  return 0;
}
