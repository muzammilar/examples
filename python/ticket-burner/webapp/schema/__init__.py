# schema.py

import const

import sqlalchemy as sa
import sqlalchemy.orm as sa_orm

from sqlalchemy import Column

# Create the engine
engine = sa.create_engine(const.DATABASE_URL)

# Create a configured "Session" class
Session = sa_orm.sessionmaker(bind=engine)

# Create a base class for declarative class definitions
class Base(sa_orm.DeclarativeBase):
    pass

# Define a class that inherits from Base and represents a table
class Tickets(Base):
    __tablename__ = "tickets"

    id_key = Column(sa.String, primary_key=True)
    name = Column(sa.String)
    assigned_to = Column(sa.String, sa.ForeignKey("users.id_key"))
    reviewer = Column(sa.String, sa.ForeignKey("users.id_key"))

class Users(Base):
    __tablename__ = "users"

    id_key = Column(sa.String, primary_key=True)
    name = Column(sa.String)

# Create all tables in the engine
Base.metadata.create_all(engine)

# Define a function to get a session
def get_session():
    return Session()
