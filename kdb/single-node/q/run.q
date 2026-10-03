/ run.q: replay a q script against the server over IPC and print it like a console session.
/ usage: q /q/run.q FILE [HOST:PORT]   (default localhost:5000)
/ One statement per line. Comment lines (starting with /) are printed as they are; every other
/ line is printed as q)LINE, sent to the server as a string and its result shown, except for
/ assignments (as on the console). The first error stops the run with exit code 1.
system "c 40 200";
args:.z.x;
h:hopen `$"::",$[1<count args;args 1;"localhost:5000"];

/ the line starts with name: (name: or name::), so the console would not print it
isassign:{[l] i:l?":"; $[(i=0) or i>=count l; 0b; all (i#l) in .Q.an,"."]};

step:{[l]
  if[0=count l; -1 ""; :()];
  if["/"=first l; -1 l; :()];
  -1 "q)",l;
  r:@[{(1b;h x)};l;{(0b;x)}];
  if[not first r; -2 "'",last r; exit 1];
  if[not isassign l; if[not (::)~last r; show last r]];
 };

step each read0 hsym `$args 0;
exit 0
