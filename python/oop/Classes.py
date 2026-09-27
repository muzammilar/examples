class Character:
    #capitalized name
    def __init__(self,name,inital_health):#they are not functions since they only operate inside class
        self.name=name  #name  is a field inside a class
        self.health=inital_health
        self.inventory=[]
        #dont return anything.
        
    def __str__(self):
        s = "Name: "+self.name
        s+= " Health: "+str(self.health)
        s+= " Inventory: "+str(self.inventory)
        return s
        
    #__ (undebar undebar ) methods are called implicitly. YOU DONT CALL THEM.   
    # when you call str(character) then it will return it.
    
    def grab(self,item):
        self.inventory.append(item)
        
    def get_health(self):
        return self.health
        
def example():
    me=Character("Bob",20)
    print str(me)
    me.grab("Pen")
    me.grab("Paper")
    print str(me)
    
example()