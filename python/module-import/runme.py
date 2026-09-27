#-------------------------------------------------------------------------------
# Name:        module1
# Purpose:
#
# Author:      Muzammil
#
# Created:     28/06/2014
# Copyright:   (c) Muzammil 2014
# Licence:     <your licence>
#-------------------------------------------------------------------------------
import module1
from mod2 import module2
import mod3.module3

def main():
    print module1.main()
    print module2.main()
    print mod3.module3.foo()

if __name__ == '__main__':
    main()
