from app.tracking.pacing import HostPacer


class FakeTime:
    def __init__(self):
        self.t = 100.0
        self.slept = []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def test_pacer_waits_between_same_host():
    ft = FakeTime()
    pacer = HostPacer(3.0, clock=ft.clock, sleep=ft.sleep)
    pacer.wait("shop.my")
    ft.t += 1.0
    pacer.wait("shop.my")
    assert ft.slept == [2.0]


def test_pacer_no_wait_for_other_host_or_after_gap():
    ft = FakeTime()
    pacer = HostPacer(3.0, clock=ft.clock, sleep=ft.sleep)
    pacer.wait("shop.my")
    pacer.wait("other.my")
    ft.t += 5.0
    pacer.wait("shop.my")
    assert ft.slept == []
