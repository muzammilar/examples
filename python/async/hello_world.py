import asyncio

# Define an asynchronous function using 'async def'
async def greet():
    print("Hello")
    # 'await' pauses the execution of 'greet' and allows other tasks to run
    # while 'asyncio.sleep(1)' simulates an I/O operation or a delay.
    await asyncio.sleep(1)
    print("World")

# Define the main asynchronous function to run our coroutines
async def main():
    print("Starting main function...")
    await greet()  # Await the completion of the 'greet' coroutine
    print("Main function finished.")

# Run the main asynchronous function
if __name__ == "__main__":
    asyncio.run(main())
