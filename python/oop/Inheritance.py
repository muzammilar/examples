class Parent():
    def __init__(self,last_name,color):
        print "Parent Contructor"
        self.last_name=last_name
        self.color=color
    
    def show_info(self):
        print "Last Name - "+self.last_name

    def show_color(self):
        print "Color - "+self.color
    
class Child(Parent):
    def __init__(self,last_name,color,number_of_toys):
        print "Child Contructor"
        Parent.__init__(self,last_name,color)
        self.number_of_toys=number_of_toys

    def show_color(self):
        print "Color - Green"

        
jane_doe=Child("DOE","White",10)
jane_doe.show_info() #prints Last Name - DOE
jane_doe.show_color() #prints Color - Green