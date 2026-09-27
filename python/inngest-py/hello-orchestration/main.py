import logging
import fastapi
import inngest
import inngest.fast_api


def create_inngest_client():
    # Create an Inngest client
    """
    Create an Inngest client.

    Returns:
        inngest.Inngest: an Inngest client
    """
    return inngest.Inngest(
        app_id="fast_api_example",
        logger=logging.getLogger("uvicorn"),
    )

def serve_functions():
    # Serve the Inngest endpoint
    inngest.fast_api.serve(app, inngest_client, [my_function])


# Create an Inngest client
inngest_client = create_inngest_client()

# Create an Inngest function (using python decorators)
@inngest_client.create_function(
    fn_id="my_function",
    # Event that triggers this function
    trigger=inngest.TriggerEvent(event="app/my_function"),
)

async def my_function(ctx: inngest.Context) -> str:
    ctx.logger.info(ctx.event)
    return "done"

# Run the main function
app = fastapi.FastAPI()

# Serve the functions
serve_functions()
