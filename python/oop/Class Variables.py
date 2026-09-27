#e.g a varible rating=[1,2,3,4,5] is a varible that we want all instances of a class to share.

class Movie():
    RATING=[1,2,3,4,5] #this is probably a constant
    def __init__(self, movie_name):
        self.name=movie_name
        
    