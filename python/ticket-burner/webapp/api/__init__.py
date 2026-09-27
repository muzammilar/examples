import uuid
import fastapi

# import models
import schema

def create_app():
    app = fastapi.FastAPI()

    @app.get("/")
    def read_root():
        return {"Hello": "World"}

    @app.get("/items/{item_id}")
    def read_item(item_id: int):
        return {"item_id": item_id}

    @app.post("/items/")
    def create_item(item: dict):
        return item

    # create a user
    @app.post("/users")
    async def create_user(request: fastapi.Request):
        data = await request.json()
        user_name = data.get("name", "")
        if not user_name:
            raise ValueError("User name is required")
        session = schema.get_session()
        user = schema.Users(id_key=str(uuid.uuid4()), name=user_name)
        session.add(user)
        session.commit()
        return user

    # view all users
    @app.get("/users")
    async def get_users():
        session = schema.get_session()
        return session.query(schema.Users).all()

    # view all tickets
    @app.get("/tickets")
    async def get_tickets():
        session = schema.get_session()
        return session.query(schema.Tickets).all()

    # create a ticket
    @app.post("/ticket")
    async def create_ticket(request: fastapi.Request):
        req_data = await request.json()
        session = schema.get_session()

        assigned_to = req_data.get("assigned_to", 0)
        reviewer = req_data.get("reviewer", 0)
        ticket_id = str(uuid.uuid4())
        name = req_data.get("name", ticket_id)
        # TODO: validation upfront

        # validate user IDs
        if not assigned_to or (assigned_to and not session.query(schema.Users).filter_by(id_key=assigned_to).first()):
            raise ValueError("Invalid assigned_to user ID")
        if not reviewer or (reviewer and not session.query(schema.Users).filter_by(id_key=reviewer).first()):
            raise ValueError("Invalid reviewer user ID")

        # create ticket
        ticket = schema.Tickets(id_key=ticket_id, name=name, assigned_to=assigned_to, reviewer=reviewer)
        session.add(ticket)
        session.commit()
        return ticket

    return app
