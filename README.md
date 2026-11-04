# Boolean Circuit Grid

Run the simulator using the existing virtual environment:

```sh
.venv/bin/python circuit_grid.py
```

The script uses Tkinter, which is already available in this environment. No extra packages are required.

The grid extends indefinitely in every direction. **Scroll vertically**, use a trackpad's horizontal scroll gesture, or **Shift-scroll horizontally** to move the viewport. Click **go to circuit** to return to the top-left occupied area (or the origin when empty).

Only visible nodes are drawn, and new areas store nodes only when interacted with. Scrolling through empty space does not grow the circuit or save file. Nodes outside the viewport continue updating normally.

The initial view defaults to 8 rows and 12 columns. These options change the initial window size, without limiting where you can build:

```sh
.venv/bin/python circuit_grid.py --rows 10 --cols 16
```

- **Left-click between neighbouring nodes** to activate or remove their edge. Connections are bidirectional; each node stores the other in its `connected_nodes` list.
- **Right-click a node** to open its menu, then choose **Switch to input/output** or **Set outbound name...**. For a named outbound, choose **Show on left when imported** or **Show on right when imported**. Inputs display **IN** at the top right. Named outbound nodes display **OUT**, their name, and an arrow showing the selected import side. Control-click also works on macOS.
- **Left-click a manual input node** to flip its value between 0 and 1. Gate-driven inputs get their values from their gates.
- **Right-click an empty grid square** to place an **AND**, **OR**, **NAND**, or **NOR** gate using nearby nodes automatically. **Left-click the gate** to rotate it 90° clockwise. Right-click the gate to change its kind or remove it.
- **Right-click a grid edge** to place a **NOT** gate in either direction. **Left-click the NOT gate** to reverse it. Right-click it and choose **Make normal edge (remove NOT)** to restore an ordinary active connection.
- **Right-click empty space away from an edge** and choose **Import circuit...** to insert a saved circuit as a component with named ports. Connect ordinary grid wires to the exposed nodes on either side of its box.
- Click **update** to advance the automaton and evaluate every gate simultaneously. Manual inputs retain their values.
- Click **reset state** to set every connected output to 0 and clear its one-update cooldown, including outputs inside imported circuits. Inputs, disconnected nodes, connections, names, and update counters are preserved. Auto update continues if it is running.
- Enter a rate beside **auto update**, then click the button to run continuously. The rate is in **updates per second**, defaults to **2**, and accepts decimals from **0.01 to 1000**. Click **stop auto update** to stop.
- Click **save** to choose a directory and filename for a `.json` setup file. The dialog starts in the script's directory and remembers the last successfully used file's directory.
- Click **load** to select a saved setup. This restores its initial view size, connections, gates and orientations, node values and types, outbound names and sides, imported circuits, cooldowns, update counters, and auto-update rate, including everything placed beyond the origin in any direction. The viewport returns to the top-left occupied area. Gate ports' original types are also preserved for later gate removal. A loaded setup starts with auto update stopped; click **auto update** to resume.

Saving briefly suspends auto update while the file dialog is open and resumes it afterwards. A cancelled or invalid load keeps the current circuit and its running/stopped state. Files are validated before loading, and saves replace the chosen file only after the complete JSON has been written successfully.

To reuse a circuit:

1. Right-click each node to expose and choose **Set outbound name...**. Give it a unique name such as `clock` or `result`, then use the node menu to choose **Show on left when imported** or **Show on right when imported** (the default). This makes it an output node. A gate-driven IN node cannot be an outbound; connect it to an output node with a normal wire and name that output instead.
2. **Save** the circuit to a JSON file.
3. In another circuit, right-click empty space and select **Import circuit...**, then choose the saved file.
4. Connect the named nodes on the left and right of the imported box to nearby grid nodes by clicking the intervening edges.

Outbound names contain 1–40 printable characters and are unique within their circuit. Use the node menu to rename an outbound, select its import side, or remove its designation. Renaming preserves the side; removing the designation resets it to the default right side. An outbound stays an output until its designation is removed. Imported ports are also outputs, and may themselves be named as outbounds with their own side choices to expose them through a containing circuit. Side choices apply when importing the saved circuit; existing imported copies retain their saved layouts.

Each component places its ports two grid spacings apart, with a narrow box between the two sides. Ports stack independently on each side, ordered by the original nodes' row and column. The height uses only `max(number of left ports, number of right ports)` node rows, with no extra header row. For example, two left ports and two right ports occupy two rows. Long labels are shortened on the compact box; right-click a port or the box to see its full name.

Only nodes beneath the actual box and the exposed ports are reserved. Unused positions beside the box remain ordinary nodes, and uncovered grid edges above, below, and along both sides can be activated, including edges between ports on the same side. Edges entering or crossing the box are blocked. Placement works anywhere on the infinite grid and requires the box and its ports to be empty; uncovered wiring and configured nodes are preserved. Right-click the box to remove it. Its exposed grid nodes then become ordinary outputs at 0, preserving external wiring.

Connections through imported ports work **in both directions**. A port and its internal outbound represent the same boolean value and cooldown. The internal outbound's automaton rule counts both its internal neighbours and the external nodes wired to its port. Parent circuits and all imported circuits read one shared previous state and update simultaneously, using the parent's update rate. Normal wires can link ports to other ports or surrounding nodes, and ports can feed gate inputs.

Each import is an independent copy of the saved circuit, including its current state and port sides. Saving a parent embeds all its imported circuits, so the original files can be moved or deleted without breaking the saved parent. Nested imports and re-exported ports are supported up to eight levels. New saves use format version 4; existing version 1, 2, and 3 files still load, with their ports defaulting to the right and their existing connections preserved. A file must have at least one named outbound before it can be imported.

Rate edits apply immediately while running. An empty or invalid rate temporarily pauses automatic updates; entering a valid rate resumes them. You can still edit nodes and connections or use the manual **update** button while auto update runs. Each automatic step follows the same rule and cooldown as a manual step. Actual speed can be lower at high requested rates or on larger grids because drawing takes time.

Each node stores a boolean value (0 or 1), its list of connected nodes, and whether it changed during the previous update. All nodes start as outputs with value 0. The value appears inside the node.

AND, OR, NAND, and NOR gates snap to the centre of the clicked square. Initially the two left corners are the gate's sources and the upper-right corner is its target; the lower-right corner is unused. Rotating moves all three ports clockwise to the next corners. Arrows on the links show the direction of data flow. A NOT gate reads one endpoint of its edge and drives the other.

Gate sources can be **input or output nodes** and keep their existing types when a gate is placed. Use a source's right-click menu to switch its type; if it is a manual input, left-click it to change its value. A source may also be a node driven by another gate or an imported circuit port. Each gate's driven target is automatically set to an **input node**, marked **IN**, and cannot be an outbound output or imported port. On every update, AND sets that target to 1 only when both sources are 1; OR sets it to 1 when either source is 1. NAND is the inverse of AND (0 only when both sources are 1), and NOR is the inverse of OR (1 only when both sources are 0). NOT sets its target to the opposite of its single source. Gates and ordinary nodes all read the values from the start of the update, then commit their new values together. Driven targets are evaluated every update without a cooldown. Save/load preserves all five gate kinds, their orientations, and source node types.

Gate links are directed connections, separate from the ordinary bidirectional neighbour lists. They do not contribute to the automaton's neighbour average. Attach ordinary wires to a gate's output-type source nodes to feed them from the automaton, and to its driven IN node to carry the result back into the automaton. Gates can be chained directly by using one gate's driven node as another gate's source; a change propagates one gate per update because all gates read the pre-update state. A source that is an output node with no ordinary neighbours holds its value until reset or reconfigured.

Only gate-driven targets have locked types and cannot be flipped manually, even when they also feed another gate. Removing a driving gate restores its target's original type; it preserves source types chosen by the user. Gates may share sources, and a node may be both a driven target and another gate's source, but it can have only one driving gate. A placement or rotation that would add a second driver leaves the circuit unchanged and displays an explanation. Gate-connected nodes use the red/green value colors; **reset state** clears gate sources that are output nodes while preserving inputs.

On each update, a connected output that did not change on the previous update follows:

```text
next value = clamp(current value + sign(neighbour average - current value), 0, 1)
```

The calculation uses the state **before** the update, and all results are applied together. The average includes directly connected input and output neighbours, excluding the node itself. `sign` returns -1 for a negative difference, 0 for zero, and 1 for a positive difference. For example, a node at 0 with neighbour values `0, 0, 1` changes to 1; a node at 1 with neighbours `1, 1, 0` changes to 0. A node whose neighbours all match its value stays unchanged.

After an output changes, it holds its value for exactly one update, then becomes eligible again. Its held value still contributes to its neighbours' averages. A skipped update consumes this cooldown even if the node is disconnected or temporarily made an input. Switching types or connections between updates does not erase the cooldown.

Disconnected outputs retain their values. Manual input values remain controlled by clicks, including during a cooldown. Gate-driven inputs follow their gate's operation on every update.

Disconnected nodes are black regardless of value. Connected nodes are red for 0 and green for 1. An active edge takes each endpoint's color on that endpoint's half, so different values produce a red/green split at its midpoint. Inactive edges appear as thin gray grid lines extending across the viewport.

To try a changing output, connect two adjacent nodes. Right-click one, choose **Switch to input**, and left-click it to set it to 1. The first **update** changes the output to 1. Flip the input back to 0: the second **update** holds the output at 1, and the third changes it to 0.

Run the model checks with:

```sh
.venv/bin/python -m unittest -v
```
# GameOfCircuit
