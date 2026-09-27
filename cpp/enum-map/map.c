/**
 * @file map.c
 * @author Muzammil Abdul Rehman (@muzammilar)
 * @brief A simple example of creating an int -> string map
 * @version 0.1
 * @date 2022-03-10
 * 
 * @copyright No Copyright (c) 2022
 * 
 */

#include <stdio.h>

enum abc {
  APPLE = 0,
  BALL,
  CAT,
  DOG,
};

int main(){

  // create a map (int -> str)
  const char *alphabet_map[] = {
    [APPLE] = "APPLE",
    [BALL] = "BALL",
    [CAT] = "CAT",
    [DOG] = "DOG"
  };

  // safe print
  printf("%s", alphabet_map[2]); 

  return 0;
}