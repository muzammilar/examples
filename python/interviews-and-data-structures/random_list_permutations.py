"""
Shuffle a set of numbers without duplicates.

Example:

// Init an array with set 1, 2, and 3.
int[] nums = {1,2,3};
Solution solution = new Solution(nums);

// Shuffle the array [1,2,3] and return its result. Any permutation of [1,2,3] must equally likely to be returned.
solution.shuffle();

// Resets the array back to its original configuration [1,2,3].
solution.reset();

// Returns the random shuffling of array [1,2,3].
solution.shuffle();
"""

import random
class Solution(object):

    def __init__(self, nums):
        self.nums = nums[:]

    def reset(self):
        return self.nums[:]

    def shuffle(self):
        temp_nums = self.nums[:]
        ret_list = []
        for i in xrange(len(temp_nums)):
            x = random.choice(temp_nums)
            temp_nums.remove(x)
            ret_list.append(x)
        return ret_list

# Your Solution object will be instantiated and called as such:
# obj = Solution(nums)
# param_1 = obj.reset()
# param_2 = obj.shuffle()