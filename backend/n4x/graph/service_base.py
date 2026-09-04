from n4x.graph.uow import GraphUnitOfWork, transactional as transactional


class ServiceBase:
    def __init__(self, uow: GraphUnitOfWork) -> None:
        self.store = uow.store
        self.uow = uow
        self.records = uow.records
