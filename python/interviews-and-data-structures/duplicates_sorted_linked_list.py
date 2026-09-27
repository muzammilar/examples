# Definition for singly-linked list.
class ListNode(object):
    def __init__(self, x):
        self.val = x
        self.next = None

class Solution(object):
    def deleteDuplicates(self, head):
        next_node = head
        while next_node is not None:
            while next_node.next is not None and next_node.next.val == next_node.val:
                next_node.next = next_node.next.next
            next_node = next_node.next
        return head