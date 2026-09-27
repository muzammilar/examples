import asyncio
import time

# Define an asynchronous function (coroutine) using 'async def'
async def say_after(delay, what):
  await asyncio.sleep(delay)  # Pause the execution of this coroutine
  print(what)

# Main asynchronous function to run the coroutines
async def main():
  print(f"started at {time.strftime('%X')}")

  # Schedule coroutines to run concurrently
  task1 = asyncio.create_task(say_after(1, 'hello'))
  task2 = asyncio.create_task(say_after(2, 'world'))

  # Wait until both tasks are completed
  await task1
  await task2

  print(f"finished at {time.strftime('%X')}")

# Run the main asynchronous function
if __name__ == "__main__":
  asyncio.run(main())
