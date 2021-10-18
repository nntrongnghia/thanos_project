import os
import pycuda.driver as cuda
import tensorrt as trt
import ctypes


def GiB(val):
    """Calculate Gibibit in bits, used to set workspace for TensorRT engine builder."""
    return val * 1 << 30


class HostDeviceMem(object):
    """
    Simple helper class to store useful data of an engine's binding
    Attributes
    ----------
    host_mem: np.ndarray
        data stored in CPU
    device_mem: pycuda.driver.DeviceAllocation
        represent data pointer in GPU
    shape: tuple
    dtype: np dtype
    name: str
        name of the binding
    """

    def __init__(self, host_mem, device_mem, shape, dtype, name=""):
        self.host = host_mem
        self.device = device_mem
        self.shape = shape
        self.dtype = dtype
        self.name = name

    def __str__(self):
        return "Host:\n" + str(self.host) + "\nDevice:\n" + str(self.device)

    def __repr__(self):
        return self.__str__()


def allocate_buffers(context, stream=None, sync_mode=True):
    """
    Read bindings' information in ExecutionContext, create pagelocked np.ndarray in CPU,
    allocate corresponding memory in GPU.
    Returns
    -------
    inputs: list[HostDeviceMem]
    outputs: list[HostDeviceMem]
    bindings: list[int]
        list of pointers in GPU for each bindings
    stream: pycuda.driver.Stream
        used for memory transfers between CPU-GPU
    """
    inputs = []
    outputs = []
    bindings = []
    if stream is None and not sync_mode:
        stream = cuda.Stream()
    for binding in context.engine:
        binding_idx = context.engine.get_binding_index(binding)
        shape = context.get_binding_shape(binding_idx)
        size = trt.volume(shape) * context.engine.max_batch_size
        dtype = trt.nptype(context.engine.get_binding_dtype(binding))
        # Allocate host and device buffers
        host_mem = cuda.pagelocked_empty(size, dtype)
        device_mem = cuda.mem_alloc(host_mem.nbytes)
        # Append the device buffer to device bindings.
        bindings.append(int(device_mem))
        # Append to the appropriate list.
        if context.engine.binding_is_input(binding):
            inputs.append(HostDeviceMem(host_mem, device_mem, shape, dtype, binding))
        else:
            outputs.append(HostDeviceMem(host_mem, device_mem, shape, dtype, binding))
    return inputs, outputs, bindings, stream


def execute_async(context, bindings, inputs, outputs, stream):
    """
    Execute an TensorRT engine.
    Parameters
    ----------
    context: tensorrt.IExecutionContext
    bindings: list[int]
        list of pointers in GPU for each bindings
    inputs: list[HostDeviceMem]
    outputs: list[HostDeviceMem]
    stream: pycuda.driver.Stream
        used for memory transfers between CPU-GPU
    Returns
    -------
    list : np.ndarray
        For each outputs of the engine
    """
    # Transfer input data to the GPU.
    [cuda.memcpy_htod_async(inp.device, inp.host, stream) for inp in inputs]
    # Run inference.
    check = context.execute_async(bindings=bindings, stream_handle=stream.handle)
    assert check, "Kernel execution failed"
    # Transfer predictions back from the GPU.
    [cuda.memcpy_dtoh_async(out.host, out.device, stream) for out in outputs]
    # Synchronize the stream
    stream.synchronize()
    # Return only the host outputs.
    for out in outputs:
        out.host = out.host.reshape(out.shape)
    return [out.host for out in outputs]


def execute_sync(context, bindings, inputs, outputs):
    """
    Execute an TensorRT engine.
    Parameters
    -----------
    context: tensorrt.IExecutionContext
    bindings: list[int]
        list of pointers in GPU for each bindings
    inputs: list[HostDeviceMem]
    outputs: list[HostDeviceMem]
    stream: pycuda.driver.Stream
        used for memory transfers between CPU-GPU
    Parameters
    ----------
    list[np.ndarray] for each outputs of the engine
    """
    # Transfer input data to the GPU.
    [cuda.memcpy_htod(inp.device, inp.host) for inp in inputs]
    # Run inference.
    check = context.execute_v2(bindings=bindings)
    assert check, "Kernel execution failed"
    # Transfer predictions back from the GPU.
    [cuda.memcpy_dtoh(out.host, out.device) for out in outputs]
    # Return only the host outputs.
    for out in outputs:
        out.host = out.host.reshape(out.shape)
    return [out.host for out in outputs]