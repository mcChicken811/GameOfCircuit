# Boolean Circuit Grid

Run the simulator using the existing virtual environment:

```sh
.venv/bin/python circuit_grid.py
```

The script uses Tkinter, which is already available in this environment. No extra packages are required.

The default grid has 8 rows and 12 columns. To choose another finite size:

```sh
.venv/bin/python circuit_grid.py --rows 10 --cols 16
```

- **Left-click between neighbouring nodes** to activate or remove their edge. Connections are bidirectional; each node stores the other in its `connected_nodes` list.
- **Right-click an ordinary node** to switch between output and input. Inputs display **IN** at the top right. Control-click also works on macOS.
- **Left-click a manual input node** to flip its value between 0 and 1. Gate-driven inputs get their values from their gates.
- **Right-click an empty grid square** to place an **AND** or **OR** gate using nearby nodes automatically. **Left-click the gate** to rotate it 90° clockwise. Right-click the gate to change its kind or remove it.
- **Right-click a grid edge** to place a **NOT** gate in either direction. **Left-click the NOT gate** to reverse it. Right-click it and choose **Make normal edge (remove NOT)** to restore an ordinary active connection.
- Click **update** to advance the automaton and evaluate every gate simultaneously. Manual inputs retain their values.
- Click **reset state** to set every connected output to 0 and clear its one-update cooldown, making it eligible on the next update. Inputs, disconnected nodes, connections, and the update counter are preserved. Auto update continues if it is running.
- Enter a rate beside **auto update**, then click the button to run continuously. The rate is in **updates per second**, defaults to **2**, and accepts decimals from **0.01 to 1000**. Click **stop auto update** to stop.

Rate edits apply immediately while running. An empty or invalid rate temporarily pauses automatic updates; entering a valid rate resumes them. You can still edit nodes and connections or use the manual **update** button while auto update runs. Each automatic step follows the same rule and cooldown as a manual step. Actual speed can be lower at high requested rates or on larger grids because drawing takes time.

Each node stores a boolean value (0 or 1), its list of connected nodes, and whether it changed during the previous update. All nodes start as outputs with value 0. The value appears inside the node.

AND/OR gates snap to the centre of the clicked square. Initially the two left corners are the gate's sources and the upper-right corner is its target; the lower-right corner is unused. Rotating moves all three ports clockwise to the next corners. Arrows on the links show the direction of data flow. A NOT gate reads one endpoint of its edge and drives the other.

Gate sources are automatically set to **output nodes**. The driven target is automatically set to an **input node**, marked **IN**. On every update, AND sets that target to 1 only when both sources are 1; OR sets it to 1 when either source is 1; NOT sets it to the opposite of its source. Gates and ordinary nodes all read the values from the start of the update, then commit their new values together. Driven targets are evaluated every update without a cooldown.

Gate links are directed connections, separate from the ordinary bidirectional neighbour lists. They do not contribute to the automaton's neighbour average. Attach ordinary wires to a gate's source nodes to feed them from the automaton, and to its driven IN node to carry the result back into the automaton. To feed another gate, connect the driven IN node by a normal wire to that gate's output-type source node. A source with no ordinary neighbours holds its value until its state is reset or its role changes.

Port types remain assigned while a gate uses them, and driven inputs cannot be flipped manually. Removing a gate restores the original types of ports no other gate uses. Gates may share sources, but a node can have only one driving gate and cannot serve as both a gate source and a driven target. A conflicting placement or rotation leaves the circuit unchanged and displays an explanation. Gate-connected nodes use the red/green value colors; **reset state** also clears gate source values and cooldowns.

On each update, a connected output that did not change on the previous update follows:

```text
next value = clamp(current value + sign(neighbour average - current value), 0, 1)
```

The calculation uses the state **before** the update, and all results are applied together. The average includes directly connected input and output neighbours, excluding the node itself. `sign` returns -1 for a negative difference, 0 for zero, and 1 for a positive difference. For example, a node at 0 with neighbour values `0, 0, 1` changes to 1; a node at 1 with neighbours `1, 1, 0` changes to 0. A node whose neighbours all match its value stays unchanged.

After an output changes, it holds its value for exactly one update, then becomes eligible again. Its held value still contributes to its neighbours' averages. A skipped update consumes this cooldown even if the node is disconnected or temporarily made an input. Switching types or connections between updates does not erase the cooldown.

Disconnected outputs retain their values. Manual input values remain controlled by clicks, including during a cooldown. Gate-driven inputs follow their gate's operation on every update.

Disconnected nodes are black regardless of value. Connected nodes are red for 0 and green for 1. An active edge takes each endpoint's color on that endpoint's half, so different values produce a red/green split at its midpoint. Inactive edges appear as thin gray grid lines. Scrollbars let you navigate grids larger than the window.

To try a changing output, connect two adjacent nodes. Right-click one to make it an input and left-click it to set it to 1. The first **update** changes the output to 1. Flip the input back to 0: the second **update** holds the output at 1, and the third changes it to 0.

Run the model checks with:

```sh
.venv/bin/python -m unittest -v
```
# GameOfCircuit
