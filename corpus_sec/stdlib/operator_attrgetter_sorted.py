from operator import attrgetter
class Obj:
    def __init__(self,x,y):
        self.x=x
        self.y=y
objs = [Obj(2,1),Obj(1,3),Obj(1,2)]
sorted_objs = sorted(objs, key=attrgetter('x','y'))
print([(o.x,o.y) for o in sorted_objs])
